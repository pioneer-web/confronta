from django.core.files import File

from administracao.models import Importacao, ItemLoteImportacao

from .batch_classification import (
    BATCH_CLASSIFIER_VERSION,
    _classify_batch_input,
    _year_hint_from_name,
)
from .batch_control import _batch_interruption_requested
from .batch_queue import _finish_item, _set_item_progress
from .batch_sicar import (
    _finalize_sicar_fingerprint,
    _register_unchanged_import,
    _resolve_sicar_uf,
)
from .exceptions import BatchInterruptionRequested
from .partitioning import normalize_uf, sicar_partition_has_rows
from .pipeline import process_import
from .prodes_filter import (
    DEFAULT_PRODES_START_YEAR,
    normalize_prodes_start_year,
)
from .sicar_tracking import (
    get_fingerprint,
    hash_file,
    mark_state_processing,
)


def _import_classified_item(item, archive, source_slug):
    lote = item.lote
    _set_item_progress(item.pk, 8, 'Confirmando arquivo antes da importação')

    current_hash = hash_file(archive)
    if item.hash_sha256 and current_hash != item.hash_sha256:
        return _finish_item(
            item,
            ItemLoteImportacao.Status.REQUER_REVISAO,
            'Arquivo alterado após análise',
            'O arquivo mudou depois da pré-análise. Ele precisa ser analisado novamente antes de qualquer promoção.',
        )

    spec, classification = _classify_batch_input(
        archive, source_slug, relative_path=item.caminho_relativo, archive_sha256=current_hash,
    )
    if spec is None:
        return _finish_item(
            item,
            ItemLoteImportacao.Status.REQUER_REVISAO,
            'Requer revisão',
            classification.get('motivo', 'Dataset não classificado.'),
        )

    if item.dataset_slug and item.dataset_slug != spec.slug:
        return _finish_item(
            item,
            ItemLoteImportacao.Status.REQUER_REVISAO,
            'Dataset mudou após análise',
            f'A pré-análise identificou {item.dataset_slug}, mas a nova leitura identificou {spec.slug}. '
            'A promoção foi bloqueada.',
        )

    item.dataset_slug = spec.slug
    item.dataset_label = spec.label
    item.hash_sha256 = current_hash

    context = {
        'lote_id': lote.pk,
        'caminho_lote': item.caminho_relativo,
        'batch_classifier_version': BATCH_CLASSIFIER_VERSION,
        'batch_classification': classification,
    }
    if source_slug == 'prodes':
        filters = (lote.resultado or {}).get('filtros') or {}
        context['prodes_ano_inicial'] = normalize_prodes_start_year(
            filters.get('ano_inicial', DEFAULT_PRODES_START_YEAR)
        )

    if source_slug == 'sicar':
        uf, uf_error = _resolve_sicar_uf(item, classification)
        if uf_error:
            item.save(update_fields=['dataset_slug', 'dataset_label', 'hash_sha256'])
            return _finish_item(
                item, ItemLoteImportacao.Status.REQUER_REVISAO,
                'UF pendente de confirmação', uf_error,
            )
        if item.uf and normalize_uf(item.uf) != uf:
            return _finish_item(
                item,
                ItemLoteImportacao.Status.REQUER_REVISAO,
                'UF mudou após análise',
                f'A análise foi preparada para {item.uf}, mas o conteúdo agora aponta para {uf}. Nada foi promovido.',
            )
        fingerprint = classification.get('fingerprint_conteudo', '')
        if item.fingerprint_conteudo and fingerprint != item.fingerprint_conteudo:
            return _finish_item(
                item,
                ItemLoteImportacao.Status.REQUER_REVISAO,
                'Conteúdo mudou após análise',
                'O fingerprint do conteúdo não é mais o mesmo da pré-análise. O arquivo precisa ser analisado novamente.',
            )
        item.uf = uf
        item.fingerprint_conteudo = fingerprint
        item.save(update_fields=['dataset_slug', 'dataset_label', 'hash_sha256', 'uf', 'fingerprint_conteudo'])
        mark_state_processing(uf, lote)

        # Outra fila pode ter promovido exatamente a mesma versão enquanto este
        # lote aguardava confirmação do administrador.
        previous = get_fingerprint(uf, spec.slug)
        if (
            previous
            and fingerprint
            and previous.hash_conteudo == fingerprint
            and sicar_partition_has_rows(spec, uf)
        ):
            imp = _register_unchanged_import(item, spec, classification, uf, archive)
            _finalize_sicar_fingerprint(item, spec, imp, changed=False)
            return _finish_item(
                item,
                ItemLoteImportacao.Status.SEM_ALTERACAO,
                'Sem alteração — banco preservado',
                'A mesma versão já foi promovida antes desta fila ser executada.',
                importacao=imp,
            )

        context.update({
            'uf': uf,
            'fingerprint_conteudo': fingerprint,
            # Sempre valida integralmente a UF no staging. Histórico de SHA não
            # substitui a confirmação territorial antes de DELETE/INSERT estadual.
            'force_validate_uf': True,
        })
    else:
        item.save(update_fields=['dataset_slug', 'dataset_label', 'hash_sha256'])
        previous_same_dataset = list(ItemLoteImportacao.objects.filter(
            lote=lote,
            dataset_slug=spec.slug,
            id__lt=item.id,
            status__in=[
                ItemLoteImportacao.Status.CONCLUIDO,
                ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
                ItemLoteImportacao.Status.SEM_ALTERACAO,
            ],
        ))
        if previous_same_dataset:
            if spec.year_partitioned:
                current_year_hint = _year_hint_from_name(item.nome_arquivo)
                previous_year_hints = {
                    _year_hint_from_name(previous.nome_arquivo) for previous in previous_same_dataset
                }
                if current_year_hint is None or current_year_hint in previous_year_hints:
                    return _finish_item(
                        item,
                        ItemLoteImportacao.Status.REQUER_REVISAO,
                        'Requer revisão',
                        f'O lote contém mais de um arquivo para {spec.label} sem anos distintos confirmáveis. '
                        'O segundo arquivo não foi aplicado automaticamente para evitar ordem de versão ambígua.',
                    )
            else:
                return _finish_item(
                    item,
                    ItemLoteImportacao.Status.REQUER_REVISAO,
                    'Requer revisão',
                    f'O lote contém mais de um arquivo para {spec.label}. O segundo arquivo não foi aplicado '
                    'automaticamente para evitar ordem de versão ambígua.',
                )

    def progress(percent, stage):
        numeric = max(0, min(100, int(percent)))
        # Até o checkpoint de publicação (82%) é seguro abortar sem alterar a
        # base ativa. No SICOR as etapas pesadas agora são preparadas no staging
        # antes deste ponto. Depois dele deixamos a transação atômica terminar.
        if numeric <= 82 and _batch_interruption_requested(lote.pk):
            raise BatchInterruptionRequested('Interrupção solicitada pelo administrador antes da publicação atômica.')
        scaled = 12 + round(numeric * 0.86)
        _set_item_progress(item.pk, min(98, scaled), stage)

    if _batch_interruption_requested(lote.pk):
        return _finish_item(
            item, ItemLoteImportacao.Status.INTERROMPIDO, 'Interrompido',
            'O lote foi interrompido antes do pipeline iniciar.', progress=item.progresso,
        )

    _set_item_progress(item.pk, 15, 'Preparando pipeline GIS')
    with archive.open('rb') as raw:
        django_file = File(raw, name=archive.name)
        imp = process_import(
            django_file,
            spec.slug,
            lote.administrador,
            context=context,
            progress_callback=progress,
        )

    if imp.status == Importacao.Status.CONCLUIDO:
        success_message = ''
        success_stage = 'Atualizado com sucesso'
        if source_slug == 'sicar':
            _finalize_sicar_fingerprint(item, spec, imp, changed=True)
            uf_report = (imp.resultado or {}).get('ufs_sicar_detectadas') or {}
            extra_ufs = list(uf_report.get('ufs_adicionais_aceitas') or [])
            if extra_ufs:
                success_message = (
                    f'UF administrativa {item.uf} atualizada. CARs de divisa também consolidados: '
                    + ', '.join(extra_ufs) + '.'
                )
        elif source_slug == 'prodes':
            filter_report = (imp.resultado or {}).get('filtro_prodes') or {}
            invalid_years = int(filter_report.get('registros_ano_invalido') or 0)
            if invalid_years:
                success_stage = 'Atualizado com pendências'
                success_message = (
                    f'{invalid_years} registro(s) sem ano válido foram excluídos somente da carga '
                    'e contabilizados no relatório. Os registros válidos foram importados.'
                )

        geometry_report = (imp.resultado or {}).get('reparo_geometrias') or {}
        geometry_pending = int(geometry_report.get('nao_reparaveis') or 0)
        if geometry_pending:
            success_stage = 'Atualizado com pendências'
            geometry_message = (
                f'{geometry_pending} geometria(s) não puderam ser reparadas com segurança; '
                'foram preservadas na RAW e excluídas somente da tabela operacional. '
                'As geometrias válidas e reparáveis foram importadas normalmente.'
            )
            success_message = f'{success_message} {geometry_message}'.strip()
        return _finish_item(
            item, ItemLoteImportacao.Status.CONCLUIDO,
            success_stage, success_message, importacao=imp,
        )
    if imp.status == Importacao.Status.INTERROMPIDO:
        return _finish_item(
            item, ItemLoteImportacao.Status.INTERROMPIDO, 'Interrompido com segurança',
            imp.motivo_rejeicao or 'Processamento interrompido antes da publicação atômica.',
            importacao=imp, progress=item.progresso,
        )
    if imp.status in {Importacao.Status.IGNORADO_DUPLICADO, Importacao.Status.SEM_ALTERACAO}:
        if source_slug == 'sicar':
            _finalize_sicar_fingerprint(item, spec, imp, changed=False)
            status = ItemLoteImportacao.Status.SEM_ALTERACAO
        else:
            status = (
                ItemLoteImportacao.Status.SEM_ALTERACAO
                if imp.status == Importacao.Status.SEM_ALTERACAO
                else ItemLoteImportacao.Status.IGNORADO_DUPLICADO
            )
        return _finish_item(
            item, status, 'Sem alteração — banco preservado',
            (
                'O conteúdo após as regras de tratamento é igual à versão conhecida. Nenhuma escrita foi feita no banco.'
                if imp.status == Importacao.Status.SEM_ALTERACAO
                else 'Arquivo já corresponde à versão conhecida. Nenhum reprocessamento foi necessário.'
            ),
            importacao=imp,
        )
    return _finish_item(
        item, ItemLoteImportacao.Status.FALHOU, 'Falhou',
        imp.motivo_rejeicao or imp.get_status_display(), importacao=imp,
    )
