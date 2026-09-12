from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from administracao.datasets import datasets_for_source
from administracao.models import (
    ItemLoteImportacao,
    LoteImportacao,
    SicarEstado,
)

from .auditoria import registrar_auditoria
from .batch_classification import BATCH_CLASSIFIER_VERSION
from .batch_common import _source_slug_from_value
from .batch_control import _cleanup_finished_batch_files


def calculate_batch_progress(lote, itens=None):
    """Progresso da fase atual, incluindo arquivos ainda não enviados no modo sequencial."""
    if itens is None:
        itens = list(lote.itens.only('id', 'progresso'))
    else:
        itens = list(itens)

    result = lote.resultado or {}
    if result.get('modo') == 'UPLOAD_SEQUENCIAL':
        expected = max(1, int(result.get('arquivos_esperados') or 1))
        # Arquivos ainda não enviados equivalem a 0%. Cada item já recebido
        # contribui com seu progresso real, portanto 1 de 6 concluído = 17%.
        return round(sum(int(item.progresso or 0) for item in itens) / expected)

    if not itens:
        return 0
    phase = str(result.get('fase') or '').upper()
    confirmed_ids = {int(value) for value in (result.get('itens_confirmados') or []) if str(value).isdigit()}
    if phase == 'IMPORTACAO' and confirmed_ids:
        phase_items = [item for item in itens if item.id in confirmed_ids]
        if phase_items:
            itens = phase_items
    return round(sum(int(item.progresso or 0) for item in itens) / len(itens))


def confirm_batch_changes(lote_id, usuario):
    """Confirma a segunda fase do lote SICAR sem reclassificar dados silenciosamente."""
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        if _source_slug_from_value(lote.fonte) != 'sicar':
            raise ValueError('A confirmação em duas etapas é exclusiva do fluxo SICAR.')
        if lote.status != LoteImportacao.Status.AGUARDANDO_CONFIRMACAO:
            raise ValueError('Este lote não está aguardando confirmação de importação.')
        ready = lote.itens.filter(status=ItemLoteImportacao.Status.PRONTO_IMPORTAR)
        confirmed_ids = list(ready.values_list('id', flat=True))
        quantidade = len(confirmed_ids)
        if not quantidade:
            raise ValueError('Nenhuma alteração está pronta para importação.')
        ready.update(
            status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
            progresso=0,
            etapa='Aguardando importação na fila',
            motivo='',
            iniciado_em=None,
            finalizado_em=None,
            importacao=None,
        )
        result = dict(lote.resultado or {})
        result['fase'] = 'IMPORTACAO'
        result['alteracoes_confirmadas'] = quantidade
        result['itens_confirmados'] = confirmed_ids
        result['progresso_percentual'] = 0
        result['confirmado_em'] = timezone.now().isoformat()
        lote.resultado = result
        lote.status = LoteImportacao.Status.PROCESSANDO
        lote.data_finalizacao = None
        lote.save(update_fields=['resultado', 'status', 'data_finalizacao'])

    registrar_auditoria(
        usuario,
        'SICAR_LOTE_IMPORTACAO_CONFIRMADA',
        'LoteImportacao',
        lote.pk,
        {'alteracoes_confirmadas': quantidade},
    )
    _update_sicar_states_for_batch(lote)
    return lote


def _sicar_completeness(lote):
    source_slug = _source_slug_from_value(lote.fonte)
    if source_slug != 'sicar':
        return {}
    expected = {spec.slug: spec.label for spec in datasets_for_source('sicar')}
    result = {}
    for slug, label in expected.items():
        qs = lote.itens.filter(dataset_slug=slug)
        result[slug] = {
            'label': label,
            'arquivos': qs.count(),
            'alteracoes_detectadas': qs.filter(status=ItemLoteImportacao.Status.PRONTO_IMPORTAR).count(),
            'concluidos': qs.filter(status__in=[
                ItemLoteImportacao.Status.CONCLUIDO,
                ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
                ItemLoteImportacao.Status.SEM_ALTERACAO,
            ]).count(),
            'falhas': qs.filter(status__in=[
                ItemLoteImportacao.Status.FALHOU,
                ItemLoteImportacao.Status.REQUER_REVISAO,
            ]).count(),
        }
    return result


def _update_sicar_states_for_batch(lote):
    if _source_slug_from_value(lote.fonte) != 'sicar':
        return
    ufs = list(lote.itens.exclude(uf='').values_list('uf', flat=True).distinct())
    for uf in ufs:
        qs = lote.itens.filter(uf=uf)
        statuses = list(qs.values_list('status', flat=True))
        state, _ = SicarEstado.objects.get_or_create(uf=uf)
        state.ultimo_lote = lote
        geometry_pending = 0
        for concluded_item in qs.filter(
            status=ItemLoteImportacao.Status.CONCLUIDO,
            importacao__isnull=False,
        ).select_related('importacao'):
            geometry_report = (concluded_item.importacao.resultado or {}).get('reparo_geometrias') or {}
            geometry_pending += int(geometry_report.get('nao_reparaveis') or 0)
        state.detalhes = {
            'lote_id': lote.pk,
            'fase': (lote.resultado or {}).get('fase', ''),
            'arquivos': qs.count(),
            'datasets': list(qs.exclude(dataset_slug='').values_list('dataset_slug', flat=True).distinct()),
            'contagens': {status: statuses.count(status) for status, _label in ItemLoteImportacao.Status.choices},
            'pendencias_geometria': geometry_pending,
        }

        own_pending = any(status in {
            ItemLoteImportacao.Status.AGUARDANDO_FILA,
            ItemLoteImportacao.Status.PENDENTE,
            ItemLoteImportacao.Status.PROCESSANDO,
        } for status in statuses)
        if own_pending:
            state.status = SicarEstado.Status.PROCESSANDO
            state.save(update_fields=['status', 'ultimo_lote', 'detalhes', 'atualizado_em'])
            continue

        # Problemas têm prioridade visual sobre alterações aguardando confirmação.
        if ItemLoteImportacao.Status.FALHOU in statuses:
            state.status = SicarEstado.Status.FALHOU
        elif ItemLoteImportacao.Status.REQUER_REVISAO in statuses or geometry_pending:
            state.status = SicarEstado.Status.ATENCAO
        elif ItemLoteImportacao.Status.PRONTO_IMPORTAR in statuses:
            state.status = SicarEstado.Status.EM_FILA
        elif ItemLoteImportacao.Status.CONCLUIDO in statuses:
            state.status = SicarEstado.Status.ATUALIZADO
        elif statuses and all(status in {
            ItemLoteImportacao.Status.SEM_ALTERACAO,
            ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
        } for status in statuses):
            state.status = SicarEstado.Status.SEM_ALTERACAO
        else:
            state.status = SicarEstado.Status.NUNCA_IMPORTADO if not state.ultima_atualizacao else state.status

        successful = list(qs.filter(status__in=[
            ItemLoteImportacao.Status.CONCLUIDO,
            ItemLoteImportacao.Status.SEM_ALTERACAO,
        ]).values_list('finalizado_em', flat=True))
        successful = [value for value in successful if value]
        if successful:
            newest_verification = max(successful)
            if not state.ultima_verificacao or newest_verification > state.ultima_verificacao:
                state.ultima_verificacao = newest_verification

        updates = [
            item.importacao.data_finalizacao
            for item in qs.filter(status=ItemLoteImportacao.Status.CONCLUIDO).select_related('importacao')
            if item.importacao and item.importacao.data_finalizacao
        ]
        if updates:
            newest_update = max(updates)
            if not state.ultima_atualizacao or newest_update > state.ultima_atualizacao:
                state.ultima_atualizacao = newest_update

        state.save(update_fields=[
            'status', 'ultima_verificacao', 'ultima_atualizacao',
            'ultimo_lote', 'detalhes', 'atualizado_em',
        ])


def retry_failed_batch_items(lote_id, usuario):
    """Reenfileira somente falhas técnicas sem transformar revisão em importação.

    Itens em REQUER_REVISAO continuam exigindo uma decisão humana. No SICAR,
    toda falha volta para a fase de pré-análise; isso evita que um arquivo que
    falhou antes da confirmação administrativa seja promovido diretamente em
    uma tentativa posterior.
    """
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        failed = lote.itens.filter(status=ItemLoteImportacao.Status.FALHOU)
        quantidade = failed.count()
        if not quantidade:
            raise ValueError('Não há itens com falha técnica para reprocessar neste lote.')

        failed.update(
            status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
            progresso=0,
            etapa='Aguardando nova análise na fila',
            motivo='',
            iniciado_em=None,
            finalizado_em=None,
            importacao=None,
            fingerprint_conteudo='',
        )

        result = dict(lote.resultado or {})
        if _source_slug_from_value(lote.fonte) == 'sicar':
            result['fase'] = 'ANALISE'
            result.pop('itens_confirmados', None)
            result.pop('alteracoes_confirmadas', None)
            lote.status = LoteImportacao.Status.ANALISANDO
        else:
            result['fase'] = 'IMPORTACAO'
            lote.status = LoteImportacao.Status.PROCESSANDO
        result['reprocessamento_falhas_em'] = timezone.now().isoformat()
        result['reprocessamento_falhas_quantidade'] = quantidade
        result['progresso_percentual'] = 0
        lote.resultado = result
        lote.data_finalizacao = None
        lote.motivo_falha = ''
        lote.save(update_fields=['resultado', 'status', 'data_finalizacao', 'motivo_falha'])

    registrar_auditoria(
        usuario,
        'LOTE_FALHAS_REENFILEIRADAS',
        'LoteImportacao',
        lote.pk,
        {'quantidade': quantidade, 'fonte': str(lote.fonte)},
    )
    lote = update_batch_status(lote.pk)
    return lote


def retry_review_batch_items(lote_id, usuario):
    """Reexecuta classificação de itens em revisão de fontes não SICAR.

    Útil quando a política de classificação foi atualizada (por exemplo PRODES
    v4). Se a ambiguidade continuar, o item voltará a REQUER_REVISAO; nenhuma
    promoção é forçada. SICAR mantém o fluxo específico de confirmação de UF.
    """
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        source_slug = _source_slug_from_value(lote.fonte)
        if source_slug == 'sicar':
            raise ValueError('No SICAR, itens em revisão devem ser corrigidos pelo fluxo de UF/camada.')
        review = lote.itens.filter(status=ItemLoteImportacao.Status.REQUER_REVISAO)
        quantidade = review.count()
        if not quantidade:
            raise ValueError('Não há itens em revisão para reanalisar neste lote.')
        review.update(
            status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
            progresso=0,
            etapa='Aguardando nova classificação na fila',
            motivo='',
            iniciado_em=None,
            finalizado_em=None,
            importacao=None,
        )
        result = dict(lote.resultado or {})
        result['fase'] = 'IMPORTACAO'
        result['reanalise_revisoes_em'] = timezone.now().isoformat()
        result['reanalise_revisoes_quantidade'] = quantidade
        result['classificador_versao'] = BATCH_CLASSIFIER_VERSION
        result['progresso_percentual'] = 0
        lote.resultado = result
        lote.status = LoteImportacao.Status.PROCESSANDO
        lote.data_finalizacao = None
        lote.save(update_fields=['resultado', 'status', 'data_finalizacao'])

    registrar_auditoria(
        usuario,
        'LOTE_REVISOES_REANALISADAS',
        'LoteImportacao',
        lote.pk,
        {'quantidade': quantidade, 'fonte': str(lote.fonte), 'classificador_versao': BATCH_CLASSIFIER_VERSION},
    )
    return update_batch_status(lote.pk)


def update_batch_status(lote_id):
    lote = LoteImportacao.objects.get(pk=lote_id)
    counts = {
        status: lote.itens.filter(status=status).count()
        for status, _ in ItemLoteImportacao.Status.choices
    }
    items = list(lote.itens.only('id', 'progresso'))
    overall_progress = calculate_batch_progress(lote, items)
    result = dict(lote.resultado or {})
    phase = str(result.get('fase') or ('ANALISE' if _source_slug_from_value(lote.fonte) == 'sicar' else 'IMPORTACAO')).upper()
    result['fase'] = phase
    result['contagens'] = counts
    result['progresso_percentual'] = overall_progress
    result['sicar_datasets'] = _sicar_completeness(lote)
    lote.resultado = result

    if result.get('interrupcao_solicitada'):
        active = counts.get(ItemLoteImportacao.Status.PROCESSANDO, 0)
        lote.status = LoteImportacao.Status.INTERROMPENDO if active else LoteImportacao.Status.INTERROMPIDO
        lote.data_finalizacao = None if active else timezone.now()
        if not active:
            result['interrompido_em'] = lote.data_finalizacao.isoformat()
            result['progresso_percentual'] = overall_progress
        lote.resultado = result
        lote.save(update_fields=['status', 'data_finalizacao', 'resultado'])
        if not active:
            _cleanup_finished_batch_files(lote)
        return lote

    pending = (
        counts.get(ItemLoteImportacao.Status.AGUARDANDO_FILA, 0)
        + counts.get(ItemLoteImportacao.Status.PENDENTE, 0)
        + counts.get(ItemLoteImportacao.Status.PROCESSANDO, 0)
    )
    if pending:
        lote.status = (
            LoteImportacao.Status.ANALISANDO
            if _source_slug_from_value(lote.fonte) == 'sicar' and phase == 'ANALISE'
            else LoteImportacao.Status.PROCESSANDO
        )
        lote.data_finalizacao = None
        lote.save(update_fields=['status', 'data_finalizacao', 'resultado'])
        _update_sicar_states_for_batch(lote)
        return lote

    # No envio sequencial, terminar um item não encerra o lote: o navegador
    # ainda precisa enviar o próximo arquivo selecionado. Só após o endpoint
    # de finalização o lote pode ir para confirmação SICAR ou estado final.
    if result.get('modo') == 'UPLOAD_SEQUENCIAL' and not result.get('sequencial_finalizado'):
        result['sequencial_aguardando_upload'] = True
        lote.resultado = result
        lote.status = (
            LoteImportacao.Status.ANALISANDO
            if _source_slug_from_value(lote.fonte) == 'sicar' and phase == 'ANALISE'
            else LoteImportacao.Status.PROCESSANDO
        )
        lote.data_finalizacao = None
        lote.save(update_fields=['status', 'data_finalizacao', 'resultado'])
        _update_sicar_states_for_batch(lote)
        return lote

    if _source_slug_from_value(lote.fonte) == 'sicar' and phase == 'ANALISE':
        ready = counts.get(ItemLoteImportacao.Status.PRONTO_IMPORTAR, 0)
        if ready:
            result['analise_concluida_em'] = timezone.now().isoformat()
            result['alteracoes_detectadas'] = ready
            result['progresso_percentual'] = 100
            lote.resultado = result
            lote.status = LoteImportacao.Status.AGUARDANDO_CONFIRMACAO
            lote.data_finalizacao = None
            lote.save(update_fields=['status', 'data_finalizacao', 'resultado'])
            _update_sicar_states_for_batch(lote)
            return lote

    report_warnings = 0
    geometry_warnings = 0
    temporal_warnings = 0
    date_warnings = 0
    concluded_items = lote.itens.filter(
        status=ItemLoteImportacao.Status.CONCLUIDO,
        importacao__isnull=False,
    ).select_related('importacao')
    for concluded_item in concluded_items:
        import_result = concluded_item.importacao.resultado or {}
        geometry_report = import_result.get('reparo_geometrias') or {}
        geometry_warnings += int(geometry_report.get('nao_reparaveis') or 0)
        if _source_slug_from_value(lote.fonte) == 'prodes':
            filter_report = import_result.get('filtro_prodes') or {}
            temporal_warnings += int(filter_report.get('registros_ano_invalido') or 0)
        date_profiles = (((import_result.get('promocao') or {}).get('normalizacao') or {}).get('normalizacao_datas') or {})
        for profile in date_profiles.values():
            date_warnings += int(profile.get('nao_reconhecidos') or 0)
            if not profile.get('preferencia_ambiguos'):
                date_warnings += int(profile.get('ambiguos') or 0)
    report_warnings = geometry_warnings + temporal_warnings + date_warnings
    result['pendencias_geometria'] = geometry_warnings
    result['pendencias_temporais'] = temporal_warnings
    result['pendencias_datas'] = date_warnings
    result['pendencias_relatorio'] = report_warnings

    has_problem = bool(
        counts.get(ItemLoteImportacao.Status.FALHOU)
        or counts.get(ItemLoteImportacao.Status.REQUER_REVISAO)
        or counts.get(ItemLoteImportacao.Status.PRONTO_IMPORTAR)
        or report_warnings
    )
    lote.status = (
        LoteImportacao.Status.CONCLUIDO_COM_PENDENCIAS
        if has_problem else LoteImportacao.Status.CONCLUIDO
    )
    lote.data_finalizacao = timezone.now()
    result['progresso_percentual'] = 100
    lote.resultado = result
    lote.save(update_fields=['status', 'data_finalizacao', 'resultado'])
    _update_sicar_states_for_batch(lote)
    _cleanup_finished_batch_files(lote)
    return lote


def recover_stale_items(minutes=120):
    cutoff = timezone.now() - timedelta(minutes=minutes)
    return ItemLoteImportacao.objects.filter(
        status=ItemLoteImportacao.Status.PROCESSANDO,
        iniciado_em__lt=cutoff,
        lote__status__in=[LoteImportacao.Status.ANALISANDO, LoteImportacao.Status.PROCESSANDO],
    ).update(
        status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
        motivo='Item recuperado automaticamente após interrupção do worker.',
        iniciado_em=None,
        progresso=0,
        etapa='Recuperado — aguardando reprocessamento',
    )
