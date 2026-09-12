import hashlib
import logging
import os
from pathlib import Path

from django.conf import settings
from django.db import transaction

from administracao.models import ItemLoteImportacao, LoteImportacao

from .auditoria import registrar_auditoria
from .batch_classification import (
    _detect_uf_hint,
    _preclassify_input_name,
)
from .batch_common import _source_slug_from_value
from .batch_storage import (
    _batch_recovery_roots,
    _create_recovery_link,
    _ensure_batch_root,
    _existing_batch_root,
)
from .batch_upload import _validate_input_extension
from .partitioning import normalize_uf


logger = logging.getLogger(__name__)


def append_sequential_upload(lote_id, uploaded_file, usuario, index=None):
    """Persiste exatamente um arquivo e só depois o libera para o worker."""
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        result = dict(lote.resultado or {})
        if result.get('modo') != 'UPLOAD_SEQUENCIAL':
            raise ValueError('Este lote não utiliza envio sequencial.')
        if result.get('sequencial_finalizado'):
            raise ValueError('O envio deste lote já foi finalizado.')
        if lote.administrador_id != usuario.id and not usuario.is_superuser:
            raise PermissionError('O usuário não possui permissão para continuar este lote.')
        if lote.itens.filter(status__in=[
            ItemLoteImportacao.Status.AGUARDANDO_FILA,
            ItemLoteImportacao.Status.PENDENTE,
            ItemLoteImportacao.Status.PROCESSANDO,
        ]).exists():
            raise ValueError('Aguarde o arquivo atual terminar antes de enviar o próximo.')

        received = int(result.get('arquivos_recebidos') or 0)
        expected = int(result.get('arquivos_esperados') or 0)
        next_index = received + 1
        requested_index = int(index or next_index)
        if requested_index != next_index:
            raise ValueError(f'O próximo arquivo esperado é o item {next_index}.')
        if next_index > expected:
            raise ValueError('Todos os arquivos previstos neste lote já foram recebidos.')
        source_slug = _source_slug_from_value(lote.fonte)
        _validate_input_extension(uploaded_file.name, source_slug)
        if settings.MAX_UPLOAD_SIZE_BYTES and uploaded_file.size > settings.MAX_UPLOAD_SIZE_BYTES:
            raise ValueError(f'O arquivo {uploaded_file.name} excede o limite configurado para upload.')

        root = _ensure_batch_root(lote)
        safe_name = Path(uploaded_file.name).name
        item_dir = root / f'item_{next_index:04d}'
        item_dir.mkdir(parents=True, exist_ok=True)
        target = item_dir / safe_name
        digest = hashlib.sha256()
        size = 0
        with target.open('wb') as dst:
            for chunk in uploaded_file.chunks():
                dst.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if not target.is_file() or target.stat().st_size != size:
            raise IOError(f'O arquivo {safe_name} não foi persistido integralmente no storage do lote.')

        relative = target.relative_to(root).as_posix()
        _create_recovery_link(target, lote.pk, relative)
        pre_dataset_slug = ''
        pre_dataset_label = ''
        pre_spec, _pre_report = _preclassify_input_name(source_slug, target)
        if pre_spec is not None:
            pre_dataset_slug = pre_spec.slug
            pre_dataset_label = pre_spec.label

        item = ItemLoteImportacao.objects.create(
            lote=lote,
            caminho_relativo=relative,
            nome_arquivo=safe_name,
            uf=(normalize_uf(result.get('uf_padrao')) or _detect_uf_hint(safe_name)) if source_slug == 'sicar' else '',
            dataset_slug=pre_dataset_slug,
            dataset_label=pre_dataset_label,
            hash_sha256=digest.hexdigest(),
            progresso=0,
            etapa='Aguardando na fila',
            status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
            motivo='',
        )
        manifest = hashlib.sha256()
        manifest.update((lote.hash_sha256 or '').encode('ascii', errors='ignore'))
        manifest.update(digest.digest())
        result['arquivos_recebidos'] = next_index
        result['arquivo_atual'] = safe_name
        result['sequencial_aguardando_upload'] = False
        lote.hash_sha256 = manifest.hexdigest()
        lote.tamanho_bytes = int(lote.tamanho_bytes or 0) + size
        lote.resultado = result
        lote.status = LoteImportacao.Status.ANALISANDO if source_slug == 'sicar' else LoteImportacao.Status.PROCESSANDO
        lote.data_finalizacao = None
        lote.save(update_fields=['hash_sha256', 'tamanho_bytes', 'resultado', 'status', 'data_finalizacao'])

    registrar_auditoria(
        usuario, 'LOTE_SEQUENCIAL_ARQUIVO_RECEBIDO', 'ItemLoteImportacao', item.pk,
        {'lote_id': lote.pk, 'indice': next_index, 'arquivo': safe_name, 'bytes': size},
    )
    return item


def _cleanup_finished_item_file(item):
    """Libera disco após sucesso real de um item sequencial.

    Falha, revisão e SICAR ainda em PRONTO_IMPORTAR preservam o ZIP. Se o
    filesystem impedir a limpeza, o próximo upload não é liberado silenciosamente:
    a tela recebe "limpeza pendente" e interrompe a sequência.
    """
    if (item.lote.resultado or {}).get('modo') != 'UPLOAD_SEQUENCIAL':
        return True
    if item.status not in {
        ItemLoteImportacao.Status.CONCLUIDO,
        ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
        ItemLoteImportacao.Status.SEM_ALTERACAO,
        ItemLoteImportacao.Status.INTERROMPIDO,
    }:
        return True
    root = _existing_batch_root(item.lote)
    if root is None:
        # Se working já foi removido, não convertemos um processamento bem-sucedido
        # em falha de limpeza. Recovery é limpo abaixo quando existir.
        root = (Path(settings.BATCH_DIR) / f'lote_{item.lote_id}').resolve()
    path = root / item.caminho_relativo
    try:
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
        for recovery_root in _batch_recovery_roots(item.lote_id):
            recovery = recovery_root / item.caminho_relativo
            recovery.unlink(missing_ok=True)
            try:
                recovery.parent.rmdir()
            except OSError:
                pass
    except OSError as exc:
        logger.exception('Falha ao liberar temporário do item %s após importação concluída.', item.pk)
        item.etapa = 'Concluído — limpeza temporária pendente'
        item.motivo = (
            (item.motivo + ' ' if item.motivo else '')
            + f'Os dados foram processados, mas o arquivo temporário não pôde ser removido: {exc}'
        )[:4000]
        item.save(update_fields=['etapa', 'motivo'])
        return False
    if item.status == ItemLoteImportacao.Status.INTERROMPIDO:
        item.etapa = 'Interrompido — temporário liberado'
    elif item.status in {ItemLoteImportacao.Status.SEM_ALTERACAO, ItemLoteImportacao.Status.IGNORADO_DUPLICADO}:
        item.etapa = 'Sem alteração — temporário liberado'
    else:
        item.etapa = 'Concluído — temporário liberado'
    item.save(update_fields=['etapa'])
    return True
