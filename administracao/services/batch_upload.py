import hashlib
import os
import shutil
from pathlib import Path

from django.conf import settings

from administracao.datasets import datasets_for_source

from .zip_security import validate_gpkg, validate_zip
from .sicor_operations import validate_operations_filename, validate_operations_header


class BatchUploadLimitError(ValueError):
    """Recusa segura e apresentável de um upload administrativo."""


MAX_FILE_MESSAGE = 'Este arquivo excede o tamanho máximo permitido.'
MAX_BATCH_TOTAL_MESSAGE = 'Este lote excede o tamanho total permitido.'
MAX_BATCH_FILES_MESSAGE = 'O lote contém mais arquivos que o permitido.'
INSUFFICIENT_SPACE_MESSAGE = 'Espaço de armazenamento insuficiente para processar este lote com segurança.'


def _declared_size(uploaded_file):
    try:
        size = int(uploaded_file.size)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise BatchUploadLimitError('Não foi possível verificar o tamanho do arquivo.') from exc
    if size < 0:
        raise BatchUploadLimitError('Não foi possível verificar o tamanho do arquivo.')
    return size


def _upload_limits():
    max_file = int(getattr(settings, 'MAX_UPLOAD_SIZE_BYTES', 0))
    max_total = int(getattr(settings, 'MAX_BATCH_UPLOAD_TOTAL_BYTES', 4 * 1024**3))
    max_files = int(getattr(settings, 'MAX_BATCH_UPLOAD_FILES', 20))
    if getattr(settings, 'DJANGO_ENV', 'development') == 'production' and min(max_file, max_total, max_files) <= 0:
        raise BatchUploadLimitError('Os limites de upload do Manage não estão configurados com segurança.')
    return max_file, max_total, max_files


def _filesystem_path(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def ensure_batch_disk_space(
    batch_bytes=0,
    *,
    extra_extraction_bytes=0,
    recovery_bytes=None,
):
    """Mantém a reserva configurada nos filesystems de working e recovery."""
    reserve = max(0, int(getattr(settings, 'MIN_FREE_DISK_BYTES', 10 * 1024**3)))
    working_needed = max(0, int(batch_bytes)) + max(0, int(extra_extraction_bytes))
    recovery_needed = (
        max(0, int(batch_bytes if recovery_bytes is None else recovery_bytes))
        + max(0, int(extra_extraction_bytes))
    )
    working = Path(getattr(settings, 'BATCH_STORAGE_DIR', settings.BATCH_DIR))
    recovery = Path(getattr(
        settings,
        'BATCH_RECOVERY_DIR',
        Path(settings.IMPORT_INBOX_DIR) / '.manage_batches' / 'recovery',
    ))
    try:
        for location, needed in ((working, working_needed), (recovery, recovery_needed)):
            free = shutil.disk_usage(_filesystem_path(location)).free
            if free - needed < reserve:
                raise BatchUploadLimitError(INSUFFICIENT_SPACE_MESSAGE)
    except OSError as exc:
        # Falha fechada: não se aceita um lote se não for possível estimar espaço.
        raise BatchUploadLimitError(INSUFFICIENT_SPACE_MESSAGE) from exc


def validate_upload_limits(
    uploaded_files,
    *,
    existing_bytes=0,
    existing_files=0,
    enforce_batch=True,
    check_disk=True,
):
    """Valida limites declarados antes de criar o lote ou gravar os arquivos."""
    files = list(uploaded_files or [])
    max_file, max_total, max_files = _upload_limits()
    sizes = [_declared_size(uploaded) for uploaded in files]

    if max_file > 0 and any(size > max_file for size in sizes):
        raise BatchUploadLimitError(MAX_FILE_MESSAGE)
    total = sum(sizes)
    if enforce_batch:
        if int(existing_files) + len(files) > max_files:
            raise BatchUploadLimitError(MAX_BATCH_FILES_MESSAGE)
        if int(existing_bytes) + total > max_total:
            raise BatchUploadLimitError(MAX_BATCH_TOTAL_MESSAGE)
    if check_disk:
        ensure_batch_disk_space(total, recovery_bytes=total)
    return total


def save_uploaded_file(uploaded_file, target, *, existing_batch_bytes=0, digest=None):
    """Grava em streaming, aplicando os mesmos limites também ao tamanho real."""
    max_file, max_total, _max_files = _upload_limits()
    digest = digest or hashlib.sha256()
    size = 0
    target = Path(target)
    try:
        with target.open('wb') as dst:
            for chunk in uploaded_file.chunks():
                next_size = size + len(chunk)
                if max_file > 0 and next_size > max_file:
                    raise BatchUploadLimitError(MAX_FILE_MESSAGE)
                if int(existing_batch_bytes) + next_size > max_total:
                    raise BatchUploadLimitError(MAX_BATCH_TOTAL_MESSAGE)
                dst.write(chunk)
                digest.update(chunk)
                size = next_size
            dst.flush()
            os.fsync(dst.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), size


def allowed_input_extensions(source_slug):
    """Extensões aceitas no lote manual de cada fonte.

    A união vem dos perfis técnicos reais já cadastrados; não transforma uma
    fonte em outra nem inventa formatos. A validação específica continua no
    pipeline do dataset antes de qualquer promoção.
    """
    source_slug = str(source_slug or '').strip().lower()
    if source_slug == 'sicar':
        return {'.zip', '.gpkg'}
    specs = datasets_for_source(source_slug)
    allowed = set()
    for spec in specs:
        if spec.data_kind == 'sicor_operacoes':
            allowed.update({'', '.gz', '.csv'})
        elif spec.data_kind in {'sicor_domain_institutions', 'sicor_domain_programs'}:
            allowed.add('.csv')
        elif spec.data_kind in {'sicor_csv', 'sicor_wkt', 'sicor_gleba_points'}:
            allowed.update({'.gz', '.csv'})
        elif spec.data_kind == 'tabular_flexible':
            allowed.update({'.csv', '.gz', '.zip'})
        elif spec.data_kind == 'spatial_flexible':
            allowed.update({'.zip', '.gpkg', '.geojson', '.json', '.gml', '.kml'})
        else:
            allowed.add('.zip')
    return allowed or {'.zip'}


def _allowed_input_extensions(source_slug):
    return allowed_input_extensions(source_slug)


def _validate_input_extension(filename, source_slug):
    suffix = Path(str(filename or '')).suffix.lower()
    if str(source_slug or '').strip().lower() == 'sicor_operacoes':
        validate_operations_filename(filename)
        return suffix
    allowed = _allowed_input_extensions(source_slug)
    if suffix not in allowed:
        expected = ', '.join(sorted(allowed))
        raise ValueError(
            f'O arquivo {Path(str(filename or '')).name or "sem nome"} não possui uma extensão permitida '
            f'para esta fonte ({expected}).'
        )
    return suffix


def _validate_input_security(path, source_slug):
    path = Path(path)
    suffix = _validate_input_extension(path.name, source_slug)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError('O arquivo recebido está vazio ou indisponível no storage do lote.')
    if source_slug == 'sicor_operacoes':
        validate_operations_header(path, path.name)
        return {
            'arquivo': path.name,
            'tamanho_bytes': path.stat().st_size,
            'validacao_lote': 'CABECALHO_SICOR_OPERACOES_VALIDO',
        }
    if suffix == '.zip':
        return validate_zip(path)
    if suffix == '.gpkg':
        return validate_gpkg(path)
    # CSV/GZIP/GeoJSON/GML/KML recebem validação estrutural no pipeline do
    # dataset. Aqui apenas confirmamos persistência e extensão permitida.
    return {'arquivo': path.name, 'tamanho_bytes': path.stat().st_size, 'validacao_lote': 'OK'}


def _save_batch_upload(uploaded_file, lote_id):
    target = Path(settings.BATCH_DIR) / f'lote_{lote_id}.zip'
    digest, size = save_uploaded_file(uploaded_file, target)
    return target, digest, size
