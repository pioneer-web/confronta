from pathlib import Path

from django.utils import timezone

from administracao.datasets import datasets_for_source
from administracao.models import Importacao, ItemLoteImportacao, SicarFingerprintCamada

from .auditoria import registrar_auditoria
from .batch_control import _batch_interruption_requested
from .batch_upload import _validate_input_security
from .exceptions import BatchInterruptionRequested
from .zip_security import run_antivirus
from .batch_classification import (
    classify_archive,
    BATCH_CLASSIFIER_VERSION,
    _detect_uf_hint,
    _filename_token_hits,
)
from .batch_queue import _finish_item, _set_item_progress
from .field_matching import norm
from .partitioning import normalize_uf, sicar_partition_has_rows
from .sicar_tracking import (
    mark_state_processing,
    get_fingerprint,
    hash_file,
    record_fingerprint,
)


def _register_unchanged_import(item, spec, classification, uf, archive):
    """Registra a verificação sem executar GDAL/PostGIS novamente."""
    now = timezone.now()
    imp = Importacao.objects.create(
        fonte=spec.fonte,
        dataset_slug=spec.slug,
        dataset_label=spec.label,
        nome_arquivo_original=archive.name,
        hash_sha256=item.hash_sha256 or hash_file(archive),
        tamanho_bytes=archive.stat().st_size,
        administrador=item.lote.administrador,
        status=Importacao.Status.SEM_ALTERACAO,
        identidade_status='CONFIRMADO',
        data_finalizacao=now,
        resultado={
            'sem_alteracao': True,
            'motivo': 'Fingerprint do conteúdo idêntico à última versão confirmada para esta UF e dataset.',
            'contexto': {
                'lote_id': item.lote_id,
                'caminho_lote': item.caminho_relativo,
                'uf': uf,
                'batch_classifier_version': BATCH_CLASSIFIER_VERSION,
                'batch_classification': classification,
            },
            'fingerprint_conteudo': item.fingerprint_conteudo,
        },
        contexto={
            'lote_id': item.lote_id,
            'caminho_lote': item.caminho_relativo,
            'uf': uf,
            'batch_classifier_version': BATCH_CLASSIFIER_VERSION,
            'batch_classification': classification,
        },
    )
    registrar_auditoria(
        item.lote.administrador,
        'SICAR_VERIFICADO_SEM_ALTERACAO',
        'Importacao',
        imp.pk,
        {'uf': uf, 'dataset': spec.slug, 'fingerprint': item.fingerprint_conteudo},
    )
    return imp


def _finalize_sicar_fingerprint(item, spec, imp, changed):
    if not item.uf or not item.fingerprint_conteudo:
        return
    record_fingerprint(
        item.uf,
        spec.slug,
        item.fingerprint_conteudo,
        item.hash_sha256,
        imp,
        changed=changed,
    )


def _resolve_sicar_uf(item, classification):
    """Resolve a UF administrativa sem exigir exclusividade territorial no arquivo.

    A UF do lote/painel organiza qual snapshot estadual está sendo atualizado.
    Arquivos SICAR de regiões de divisa podem trazer COD_IMOVEL de UFs vizinhas;
    isso é válido desde que a UF administrativa também esteja presente.
    """
    report = classification.get('sicar_uf') or {}
    detected = sorted({
        normalize_uf(value) for value in report.get('detectadas', []) if normalize_uf(value)
    })
    auto_uf = normalize_uf(report.get('uf'))
    selected_uf = normalize_uf(item.uf)
    hint_uf = _detect_uf_hint(item.caminho_relativo) or _detect_uf_hint(item.nome_arquivo)

    # Preferimos a escolha explícita do administrador. A dica do caminho/nome só
    # entra quando não existe seleção explícita. Em ambos os casos, se a amostra
    # conseguiu identificar UFs, a UF administrativa precisa aparecer nela.
    administrative_uf = selected_uf or hint_uf
    if administrative_uf and detected:
        if administrative_uf not in detected:
            return '', (
                f'A UF administrativa {administrative_uf} não foi encontrada no conteúdo amostrado '
                f'({", ".join(detected)}). O arquivo foi mantido para revisão e nada foi promovido.'
            )
        return administrative_uf, ''

    if administrative_uf:
        # A amostra pode ser inconclusiva; o pipeline confirma integralmente o
        # staging antes da promoção e bloqueará a carga se a UF não estiver lá.
        return administrative_uf, ''

    if auto_uf:
        return auto_uf, ''

    if len(detected) == 1:
        return detected[0], ''

    if len(detected) > 1:
        return '', (
            'Foram detectadas várias UFs no conteúdo (' + ', '.join(detected) + '). '
            'Selecione a UF administrativa deste lote para indicar qual snapshot estadual '
            'está sendo atualizado. As UFs vizinhas serão aceitas como registros de divisa.'
        )

    return '', (
        'A UF não pôde ser identificada com segurança. Selecione o estado no relatório '
        'do lote e reprocesse o item.'
    )


def _same_partition_item(lote, item, uf, dataset_slug):
    return (
        ItemLoteImportacao.objects.filter(
            lote=lote,
            uf=uf,
            dataset_slug=dataset_slug,
            id__lt=item.id,
            status__in=[
                ItemLoteImportacao.Status.PRONTO_IMPORTAR,
                ItemLoteImportacao.Status.CONCLUIDO,
                ItemLoteImportacao.Status.SEM_ALTERACAO,
                ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
            ],
        )
        .order_by('-id')
        .first()
    )


def _sicar_spec_hint_from_filename(filename):
    """Retorna um dataset somente quando o nome do ZIP o discrimina sem empate.

    Isto não confirma um arquivo novo. O atalho só é usado quando o SHA-256 do
    arquivo é exatamente igual ao último arquivo já confirmado para UF+dataset.
    """
    ranked = []
    for spec in datasets_for_source('sicar'):
        hits = _filename_token_hits(filename, spec)
        if not hits:
            continue
        lengths = [len(norm(token)) for token in hits]
        ranked.append((max(lengths, default=0), len(hits), sum(lengths), spec))
    if not ranked:
        return None
    ranked.sort(key=lambda row: row[:3], reverse=True)
    best_rank = ranked[0][:3]
    leaders = [row[3] for row in ranked if row[:3] == best_rank]
    return leaders[0] if len(leaders) == 1 else None


def _try_sicar_sha_shortcut(item, archive):
    uf = normalize_uf(item.uf) or _detect_uf_hint(item.caminho_relativo) or _detect_uf_hint(item.nome_arquivo)
    if not uf or not item.hash_sha256:
        return None
    spec = _sicar_spec_hint_from_filename(archive.name)
    if not spec:
        return None
    previous = get_fingerprint(uf, spec.slug)
    if not previous or not previous.hash_arquivo or previous.hash_arquivo != item.hash_sha256:
        return None
    # GeoPackages SICAR atuais trazem DICIONARIO com metadados oficiais. Se a
    # versão histórica foi confirmada antes de o Manage registrar esse catálogo,
    # não usamos o atalho SHA: fazemos uma inspeção leve uma única vez para
    # capturar os metadados, sem reprocessar o PostGIS quando o conteúdo for igual.
    if Path(archive).suffix.lower() == '.gpkg':
        previous_result = (previous.ultima_importacao.resultado or {}) if previous.ultima_importacao_id else {}
        previous_metadata = previous_result.get('metadados_sicar') or {}
        if not previous_metadata.get('present'):
            return None
    if not sicar_partition_has_rows(spec, uf):
        # A fonte é conhecida, mas a partição operacional não está presente.
        # Não pulamos a carga: o arquivo volta ao fluxo completo para reparar a base.
        return None

    classification = {
        'status': 'CLASSIFICADO',
        'dataset_slug': spec.slug,
        'dataset_label': spec.label,
        'criterio': 'SHA256_ESTADUAL_JA_CONFIRMADO',
        'classificador_versao': BATCH_CLASSIFIER_VERSION,
        'sicar_uf': {'uf': uf, 'detectadas': [uf], 'confiavel': True},
        'fingerprint_conteudo': previous.hash_conteudo,
    }
    item.uf = uf
    item.dataset_slug = spec.slug
    item.dataset_label = spec.label
    item.fingerprint_conteudo = previous.hash_conteudo
    item.save(update_fields=['uf', 'dataset_slug', 'dataset_label', 'fingerprint_conteudo'])
    imp = _register_unchanged_import(item, spec, classification, uf, archive)
    _finalize_sicar_fingerprint(item, spec, imp, changed=False)
    return _finish_item(
        item,
        ItemLoteImportacao.Status.SEM_ALTERACAO,
        'Sem alteração — SHA-256 confirmado',
        'Arquivo idêntico à última versão confirmada para esta UF e camada. A extração GIS e o PostGIS foram ignorados.',
        importacao=imp,
    )


def _analyze_sicar_item(item, archive):
    lote = item.lote
    if _batch_interruption_requested(lote.pk):
        raise BatchInterruptionRequested('Interrupção solicitada antes da pré-análise SICAR.')
    _set_item_progress(item.pk, 8, 'Validando segurança e identificando o arquivo')
    # Arquivo sem alteração também passa pela política de segurança. O fato de o
    # conteúdo ser conhecido não autoriza pular validação/antimalware.
    _validate_input_security(archive, 'sicar')
    run_antivirus(archive)

    shortcut = _try_sicar_sha_shortcut(item, archive)
    if shortcut is not None:
        return shortcut

    spec, classification = classify_archive(
        archive, 'sicar', relative_path=item.caminho_relativo,
        archive_sha256=item.hash_sha256,
    )
    if spec is None:
        return _finish_item(
            item,
            ItemLoteImportacao.Status.REQUER_REVISAO,
            'Requer revisão',
            classification.get('motivo', 'Dataset não classificado.'),
        )

    item.dataset_slug = spec.slug
    item.dataset_label = spec.label
    item.hash_sha256 = item.hash_sha256 or hash_file(archive)
    item.fingerprint_conteudo = classification.get('fingerprint_conteudo', '')
    uf, uf_error = _resolve_sicar_uf(item, classification)
    if uf_error:
        item.save(update_fields=['dataset_slug', 'dataset_label', 'hash_sha256', 'fingerprint_conteudo'])
        return _finish_item(
            item, ItemLoteImportacao.Status.REQUER_REVISAO,
            'UF pendente de confirmação', uf_error,
        )

    item.uf = uf
    item.save(update_fields=['dataset_slug', 'dataset_label', 'hash_sha256', 'fingerprint_conteudo', 'uf'])
    mark_state_processing(uf, lote)
    _set_item_progress(item.pk, 45, f'{uf} identificado — comparando com a versão anterior')
    if _batch_interruption_requested(lote.pk):
        raise BatchInterruptionRequested('Interrupção solicitada durante a pré-análise SICAR.')

    if item.fingerprint_conteudo:
        same_content_other_state = SicarFingerprintCamada.objects.filter(
            dataset_slug=spec.slug,
            hash_conteudo=item.fingerprint_conteudo,
        ).exclude(uf=uf).first()
        if same_content_other_state:
            return _finish_item(
                item,
                ItemLoteImportacao.Status.REQUER_REVISAO,
                'Conteúdo associado a outra UF',
                f'O mesmo conteúdo já foi confirmado anteriormente para {same_content_other_state.uf}, '
                f'mas este item está associado a {uf}. A atualização foi bloqueada para revisão.',
            )

    previous_in_batch = _same_partition_item(lote, item, uf, spec.slug)
    if previous_in_batch and previous_in_batch.fingerprint_conteudo and item.fingerprint_conteudo:
        if previous_in_batch.fingerprint_conteudo == item.fingerprint_conteudo:
            return _finish_item(
                item,
                ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
                'Duplicado no próprio lote',
                f'O mesmo conteúdo de {spec.label} para {uf} já aparece anteriormente neste lote.',
            )
        return _finish_item(
            item,
            ItemLoteImportacao.Status.REQUER_REVISAO,
            'Duas versões da mesma UF/camada',
            f'O lote contém duas versões diferentes de {spec.label} para {uf}. '
            'A segunda versão foi bloqueada porque não há uma data de referência confiável '
            'para decidir automaticamente qual arquivo deve prevalecer.',
        )

    previous = get_fingerprint(uf, spec.slug)
    if (
        previous
        and item.fingerprint_conteudo
        and previous.hash_conteudo == item.fingerprint_conteudo
        and sicar_partition_has_rows(spec, uf)
    ):
        imp = _register_unchanged_import(item, spec, classification, uf, archive)
        _finalize_sicar_fingerprint(item, spec, imp, changed=False)
        return _finish_item(
            item,
            ItemLoteImportacao.Status.SEM_ALTERACAO,
            'Sem alteração — banco preservado',
            'Conteúdo igual à última versão confirmada. O PostGIS não será reprocessado.',
            importacao=imp,
        )

    return _finish_item(
        item,
        ItemLoteImportacao.Status.PRONTO_IMPORTAR,
        'Alteração detectada — aguardando confirmação',
        'Nova versão identificada. Nenhuma tabela operacional foi alterada nesta etapa.',
    )
