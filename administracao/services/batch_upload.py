import hashlib
import os
from pathlib import Path

from django.conf import settings

from administracao.datasets import datasets_for_source

from .zip_security import validate_gpkg, validate_zip


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
        if spec.data_kind in {'sicor_csv', 'sicor_wkt', 'sicor_gleba_points'}:
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
    if suffix == '.zip':
        return validate_zip(path)
    if suffix == '.gpkg':
        return validate_gpkg(path)
    # CSV/GZIP/GeoJSON/GML/KML recebem validação estrutural no pipeline do
    # dataset. Aqui apenas confirmamos persistência e extensão permitida.
    return {'arquivo': path.name, 'tamanho_bytes': path.stat().st_size, 'validacao_lote': 'OK'}


def _save_batch_upload(uploaded_file, lote_id):
    target = Path(settings.BATCH_DIR) / f'lote_{lote_id}.zip'
    sha = hashlib.sha256()
    size = 0
    with target.open('wb') as dst:
        for chunk in uploaded_file.chunks():
            size += len(chunk)
            sha.update(chunk)
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    return target, sha.hexdigest(), size
