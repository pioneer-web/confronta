import shutil
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from administracao.models import ItemLoteImportacao, LoteImportacao

from .auditoria import registrar_auditoria
from .batch_storage import (
    _batch_recovery_roots,
    _batch_root_candidates,
    _cleanup_batch_paths,
)


def _batch_interruption_requested(lote_id):
    lote = LoteImportacao.objects.filter(pk=lote_id).values('status', 'resultado').first()
    if not lote:
        return True
    result = lote.get('resultado') or {}
    return bool(
        result.get('interrupcao_solicitada')
        or lote.get('status') in {LoteImportacao.Status.INTERROMPENDO, LoteImportacao.Status.INTERROMPIDO}
    )


def request_batch_interruption(lote_id, usuario):
    """Solicita interrupção cooperativa sem matar o worker nem tocar na base ativa.

    Itens ainda não iniciados são encerrados imediatamente. Um item já em
    processamento será interrompido no próximo checkpoint anterior à promoção
    atômica. Se a publicação já tiver começado, ela termina com segurança e o
    lote para antes de qualquer item seguinte.
    """
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        if not lote.pode_interromper:
            raise ValueError('Este lote já está finalizado e não pode ser interrompido.')

        now = timezone.now()
        result = dict(lote.resultado or {})
        result['interrupcao_solicitada'] = True
        result['interrupcao_solicitada_em'] = now.isoformat()
        result['interrupcao_solicitada_por'] = getattr(usuario, 'email', '') or str(getattr(usuario, 'pk', ''))
        lote.resultado = result

        waiting = lote.itens.filter(status__in=[
            ItemLoteImportacao.Status.AGUARDANDO_FILA,
            ItemLoteImportacao.Status.PENDENTE,
            ItemLoteImportacao.Status.PRONTO_IMPORTAR,
        ])
        waiting.update(
            status=ItemLoteImportacao.Status.INTERROMPIDO,
            etapa='Interrompido pelo administrador',
            motivo='O lote foi interrompido antes deste arquivo iniciar a promoção.',
            finalizado_em=now,
        )
        active = lote.itens.filter(status=ItemLoteImportacao.Status.PROCESSANDO).exists()
        lote.status = LoteImportacao.Status.INTERROMPENDO if active else LoteImportacao.Status.INTERROMPIDO
        lote.data_finalizacao = None if active else now
        lote.save(update_fields=['resultado', 'status', 'data_finalizacao'])

    registrar_auditoria(
        usuario, 'LOTE_INTERRUPCAO_SOLICITADA', 'LoteImportacao', lote.pk,
        {'fonte': str(lote.fonte), 'item_em_processamento': active},
    )
    if not active:
        _cleanup_finished_batch_files(lote)
    return lote


def delete_batch_record(lote_id, usuario):
    """Remove o lote da fila/painel sem apagar dados publicados.

    A remoção é lógica (soft delete) para que um worker que ainda esteja
    encerrando uma etapa possa perceber a solicitação de interrupção sem
    perder as referências do lote/item. Itens não iniciados são interrompidos
    imediatamente. Um item já em execução termina/aborta no próximo checkpoint
    seguro do pipeline. Nenhuma tabela operacional/RAW já publicada é apagada.
    """
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        if lote.oculto_painel:
            return lote.pk

        now = timezone.now()
        result = dict(lote.resultado or {})
        result['interrupcao_solicitada'] = True
        result.setdefault('interrupcao_solicitada_em', now.isoformat())
        result.setdefault(
            'interrupcao_solicitada_por',
            getattr(usuario, 'email', '') or str(getattr(usuario, 'pk', '')),
        )
        result['removido_da_fila'] = True
        result['removido_da_fila_em'] = now.isoformat()
        result['removido_da_fila_por'] = getattr(usuario, 'email', '') or str(getattr(usuario, 'pk', ''))
        lote.resultado = result

        waiting = lote.itens.filter(status__in=[
            ItemLoteImportacao.Status.AGUARDANDO_FILA,
            ItemLoteImportacao.Status.PENDENTE,
            ItemLoteImportacao.Status.PRONTO_IMPORTAR,
        ])
        waiting.update(
            status=ItemLoteImportacao.Status.INTERROMPIDO,
            etapa='Removido da fila pelo administrador',
            motivo='O lote foi removido da fila antes deste arquivo iniciar.',
            finalizado_em=now,
        )

        active = lote.itens.filter(status=ItemLoteImportacao.Status.PROCESSANDO).exists()
        previous_status = lote.status
        if active:
            lote.status = LoteImportacao.Status.INTERROMPENDO
            lote.data_finalizacao = None
        elif lote.status in {
            LoteImportacao.Status.RECEBIDO,
            LoteImportacao.Status.PREPARANDO,
            LoteImportacao.Status.ANALISANDO,
            LoteImportacao.Status.AGUARDANDO_CONFIRMACAO,
            LoteImportacao.Status.PROCESSANDO,
            LoteImportacao.Status.INTERROMPENDO,
        }:
            lote.status = LoteImportacao.Status.INTERROMPIDO
            lote.data_finalizacao = now

        lote.oculto_painel = True
        lote.removido_painel_em = now
        lote.save(update_fields=[
            'resultado', 'status', 'data_finalizacao', 'oculto_painel', 'removido_painel_em'
        ])
        lote_pk = lote.pk
        fonte = str(lote.fonte)

    # Não remover arquivos enquanto um item ainda estiver efetivamente em uso.
    # O worker fará a limpeza ao terminar/interromper no checkpoint seguro.
    if not active:
        _cleanup_batch_paths(lote)

    registrar_auditoria(
        usuario, 'LOTE_REMOVIDO_DA_FILA', 'LoteImportacao', lote_pk,
        {
            'fonte': fonte,
            'status_anterior': previous_status,
            'item_em_processamento': active,
            'dados_publicados_preservados': True,
            'remocao_logica': True,
        },
    )
    return lote_pk


def _cleanup_finished_batch_files(lote):
    # Política conservadora: só limpamos quando TODOS os itens estão em estados
    # finais que não podem ser reprocessados/revisados. Uma situação nova ou um
    # status futuro fica preservado por padrão em vez de apagar o lote.
    cleanup_safe_statuses = [
        ItemLoteImportacao.Status.CONCLUIDO,
        ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
        ItemLoteImportacao.Status.SEM_ALTERACAO,
        ItemLoteImportacao.Status.INTERROMPIDO,
    ]
    if lote.itens.exclude(status__in=cleanup_safe_statuses).exists():
        return
    if (lote.resultado or {}).get('modo') == 'PASTA_MONITORADA':
        path = Path(lote.extracted_path)
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        if path.exists():
            for marker in path.glob(f'PROCESSANDO_CONFRONTA_{lote.pk}.txt'):
                marker.unlink(missing_ok=True)
            result_marker = path / f'RESULTADO_CONFRONTA_{lote.pk}.txt'
            result_marker.write_text(
                f'Lote #{lote.pk}\nStatus: {lote.get_status_display()}\nFinalizado em: {lote.data_finalizacao}\n',
                encoding='utf-8',
            )
        return
    for path in _batch_root_candidates(lote):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    if lote.quarantine_path:
        path = Path(lote.quarantine_path)
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        if path.exists() and path.is_file():
            path.unlink(missing_ok=True)
    for recovery_root in _batch_recovery_roots(lote.pk):
        if recovery_root.exists():
            shutil.rmtree(recovery_root, ignore_errors=True)
