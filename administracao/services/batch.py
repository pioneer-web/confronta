import hashlib
import csv
import fnmatch
import gzip
import io
from datetime import timedelta
import logging
import os
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from administracao.constants import FONTE_SLUGS, BATCH_FONTE_SLUGS
from administracao.datasets import datasets_for_source
from administracao.models import (
    Importacao, ItemLoteImportacao, LoteImportacao, SicarEstado, SicarFingerprintCamada,
)
from .auditoria import registrar_auditoria
from .dataset_identity import score_layer
from .exceptions import BatchInterruptionRequested
from .field_matching import norm
from .extraction import extract_zip_safely
from .gis_inspector import inspect_all, inspect_dataset
from .partitioning import UF_CODES, normalize_uf, sicar_partition_has_rows
from .sicar_tracking import (
    detect_sicar_uf_from_layer, fingerprint_layer_content, get_fingerprint,
    mark_state_processing, record_fingerprint, hash_file,
)
from .pipeline import process_import
from .prodes_filter import DEFAULT_PRODES_START_YEAR, normalize_prodes_start_year
from .zip_security import run_antivirus, validate_zip, validate_gpkg

from .batch_storage import (
    _batch_root_candidates,
    _batch_recovery_roots,
    _set_batch_root,
    _existing_batch_root,
    _ensure_batch_root,
    _batch_recovery_root,
    _create_recovery_link,
    _restore_from_recovery,
)
from .batch_upload import (
    allowed_input_extensions,
    _allowed_input_extensions,
    _validate_input_extension,
    _validate_input_security,
    _save_batch_upload,
)

from .batch_classification import (
    _detect_uf,
    _detect_uf_hint,
    _filename_token_hits,
    _filename_pattern_hits,
    _name_rank,
    _structural_rank,
    _public_candidates,
)

from .batch_classification import (
    BATCH_CLASSIFIER_VERSION,
    _trusted_batch_history,
    _previous_signatures,
)

from .batch_inbox import (
    _manifest_hash,
    _source_folder,
    _folder_already_claimed,
    _claim_folder_marker,
    _mark_folder_preparation_error,
    create_batch_from_folder,
    scan_inbox_once,
)

from .batch_storage import (
    _item_archive_path,
    _cleanup_batch_paths,
)

from .batch_queue import (
    claim_next_item,
    _set_item_progress,
    _finish_item,
)

from .batch_creation import (
    create_batch,
    create_batch_from_uploads,
    create_sequential_batch,
)

from .batch_classification import classify_archive

from .batch_classification import (
    _preclassify_input_name,
    _sicor_filename_matches,
    _sicor_header_tokens,
    _classify_sicor_input,
    _classify_batch_input,
    _year_hint_from_name,
)

from .batch_sicar import (
    _register_unchanged_import,
    _finalize_sicar_fingerprint,
    _resolve_sicar_uf,
    _same_partition_item,
    _sicar_spec_hint_from_filename,
    _try_sicar_sha_shortcut,
)

from .batch_control import (
    _batch_interruption_requested,
    request_batch_interruption,
    delete_batch_record,
    _cleanup_finished_batch_files,
)

from .batch_sicar import _analyze_sicar_item

from .batch_processing import _import_classified_item

from .batch_common import _source_slug_from_value
from .batch_sequential import (
    append_sequential_upload,
    _cleanup_finished_item_file,
)

from .batch_status import (
    calculate_batch_progress,
    confirm_batch_changes,
    _sicar_completeness,
    _update_sicar_states_for_batch,
    retry_failed_batch_items,
    retry_review_batch_items,
    update_batch_status,
    recover_stale_items,
)

logger = logging.getLogger(__name__)

# Versão da política de classificação em lote. Lotes antigos (v1) podem ter
# usado semelhança estrutural genérica para escolher o destino. Eles não devem
# influenciar a nova classificação nem impedir o reprocessamento corretivo.













































def finalize_sequential_batch(lote_id, usuario):
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        result = dict(lote.resultado or {})
        if result.get('modo') != 'UPLOAD_SEQUENCIAL':
            raise ValueError('Este lote não utiliza envio sequencial.')
        expected = int(result.get('arquivos_esperados') or 0)
        received = int(result.get('arquivos_recebidos') or 0)
        if received != expected:
            raise ValueError(f'O lote recebeu {received} de {expected} arquivo(s).')
        if lote.itens.filter(status__in=[
            ItemLoteImportacao.Status.AGUARDANDO_FILA,
            ItemLoteImportacao.Status.PENDENTE,
            ItemLoteImportacao.Status.PROCESSANDO,
        ]).exists():
            raise ValueError('O último arquivo ainda está sendo processado.')
        result['sequencial_finalizado'] = True
        result['sequencial_aguardando_upload'] = False
        result['envio_finalizado_em'] = timezone.now().isoformat()
        lote.resultado = result
        lote.save(update_fields=['resultado'])
    registrar_auditoria(
        usuario, 'LOTE_SEQUENCIAL_FINALIZADO', 'LoteImportacao', lote.pk,
        {'arquivos_recebidos': received},
    )
    return update_batch_status(lote.pk)














































































def process_batch_item(item):
    item = ItemLoteImportacao.objects.select_related('lote', 'lote__administrador').get(pk=item.pk)
    lote = item.lote
    source_slug = _source_slug_from_value(lote.fonte)
    phase = str((lote.resultado or {}).get('fase') or ('ANALISE' if source_slug == 'sicar' else 'IMPORTACAO')).upper()
    try:
        if _batch_interruption_requested(lote.pk):
            _finish_item(
                item, ItemLoteImportacao.Status.INTERROMPIDO, 'Interrompido',
                'O lote foi interrompido antes deste item iniciar.', progress=item.progresso,
            )
        else:
            archive = _item_archive_path(item)
            if source_slug == 'sicar' and phase == 'ANALISE':
                _analyze_sicar_item(item, archive)
            else:
                _import_classified_item(item, archive, source_slug)
    except BatchInterruptionRequested as exc:
        _finish_item(item, ItemLoteImportacao.Status.INTERROMPIDO, 'Interrompido com segurança', str(exc), progress=item.progresso)
    except Exception as exc:
        logger.exception('Falha no item %s do lote %s', item.pk, lote.pk)
        _finish_item(item, ItemLoteImportacao.Status.FALHOU, 'Falhou', str(exc))
    update_batch_status(lote.pk)
    finished_item = ItemLoteImportacao.objects.select_related('lote').get(pk=item.pk)
    _cleanup_finished_item_file(finished_item)
    return finished_item
