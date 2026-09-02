import hashlib
import re
from collections import Counter
from pathlib import Path

import pyogrio
from django.utils import timezone

from administracao.models import SicarEstado, SicarFingerprintCamada
from administracao.datasets import datasets_for_source
from .field_matching import find_matching_field
from .partitioning import UF_CODES, UF_NAMES, normalize_uf

# Componentes que alteram efetivamente geometria, atributos, CRS ou encoding.
# Índices auxiliares (.qix/.sbn/.sbx), XML de metadados e .shx regenerável não
# participam do fingerprint para não disparar uma carga territorial sem mudança
# semântica do dataset.
_SHAPEFILE_FINGERPRINT_EXTENSIONS = {'.shp', '.dbf', '.prj', '.cpg'}



def hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as src:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_files(path):
    path = Path(path)
    if path.suffix.lower() != '.shp':
        return [path]
    files = []
    stem = path.stem.lower()
    for candidate in path.parent.iterdir():
        if not candidate.is_file():
            continue
        if candidate.stem.lower() == stem and candidate.suffix.lower() in _SHAPEFILE_FINGERPRINT_EXTENSIONS:
            files.append(candidate)
    return sorted(files, key=lambda p: (p.suffix.lower(), p.name.lower()))


def _update_component_hash(digest, path):
    """Atualiza o hash ignorando apenas metadado volátil conhecido.

    O cabeçalho DBF usa os bytes 1..3 para registrar a data da última escrita.
    Exportar novamente a mesma tabela em outro dia pode alterar somente esses
    três bytes. Zerá-los no fingerprint evita uma reimportação sem mudança real
    nos atributos. Os demais bytes continuam sendo comparados integralmente.
    """
    suffix = path.suffix.lower()
    with path.open('rb') as src:
        offset = 0
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            if suffix == '.dbf' and offset < 4 and offset + len(chunk) > 1:
                mutable = bytearray(chunk)
                start = max(1, offset)
                end = min(4, offset + len(chunk))
                for absolute in range(start, end):
                    mutable[absolute - offset] = 0
                chunk = bytes(mutable)
            digest.update(chunk)
            offset += len(chunk)


def fingerprint_layer_content(layer):
    """Assinatura estável do conteúdo vetorial, independente do ZIP.

    Em Shapefile, entram somente componentes que afetam geometria, atributos,
    CRS ou encoding (SHP/DBF/PRJ/CPG). Arquivos auxiliares regeneráveis e a data
    interna do DBF não transformam, sozinhos, um pacote em nova versão.
    """
    dataset_path = Path(layer['dataset_path'])
    digest = hashlib.sha256()
    files = _dataset_files(dataset_path)
    for path in files:
        # O nome-base do arquivo não participa da assinatura. Renomear o SHP
        # dentro de um novo ZIP não deve, sozinho, provocar reimportação.
        component = path.suffix.lower() if dataset_path.suffix.lower() == '.shp' else 'dataset'
        digest.update(component.encode('ascii', errors='replace'))
        digest.update(b'\0')
        _update_component_hash(digest, path)
        digest.update(b'\0')
    return digest.hexdigest()


def _uf_from_car(value):
    match = re.match(r'^\s*([A-Za-z]{2})-', str(value or ''))
    if not match:
        return ''
    return normalize_uf(match.group(1))


def _sample_values(path, layer_name, field, feature_count, encoding=None):
    """Amostra início e fim do arquivo para evitar falso estado em arquivo nacional."""
    chunks = []
    count = int(feature_count or 0)
    first_size = min(count, 1000) if count > 0 else 1000
    try:
        frame = pyogrio.read_dataframe(
            path, layer=layer_name, columns=[field], read_geometry=False,
            max_features=first_size, encoding=encoding,
        )
        chunks.extend(frame[field].tolist())
    except Exception:
        return []

    if count > first_size:
        tail_size = min(1000, count)
        skip = max(0, count - tail_size)
        try:
            frame = pyogrio.read_dataframe(
                path, layer=layer_name, columns=[field], read_geometry=False,
                skip_features=skip, max_features=tail_size, encoding=encoding,
            )
            chunks.extend(frame[field].tolist())
        except Exception:
            pass
    return chunks


def detect_sicar_uf_from_layer(layer, spec):
    if spec.fonte_slug != 'sicar':
        return {'aplicavel': False, 'uf': '', 'detectadas': []}

    cod_spec = next((field for field in spec.fields if field.canonical == 'cod_imovel'), None)
    if not cod_spec:
        return {'aplicavel': True, 'uf': '', 'detectadas': [], 'motivo': 'Campo lógico cod_imovel não configurado.'}

    field = find_matching_field(layer.get('fields', []), cod_spec.aliases)
    if not field:
        return {'aplicavel': True, 'uf': '', 'detectadas': [], 'motivo': 'COD_IMOVEL não localizado para identificar a UF.'}

    values = _sample_values(
        layer['dataset_path'], layer.get('layer_name'), field,
        layer.get('feature_count_reported'), layer.get('encoding_override') or layer.get('source_encoding'),
    )
    counts = Counter(filter(None, (_uf_from_car(value) for value in values)))
    detectadas = sorted(counts)
    return {
        'aplicavel': True,
        'campo': field,
        'uf': detectadas[0] if len(detectadas) == 1 else '',
        'detectadas': detectadas,
        'distribuicao_amostra': [{'uf': uf, 'registros': counts[uf]} for uf in detectadas],
        'amostrados': sum(counts.values()),
        'confiavel': len(detectadas) == 1 and bool(counts),
        'motivo': '' if len(detectadas) == 1 else (
            'Mais de uma UF foi detectada na amostra.' if len(detectadas) > 1
            else 'Não foi possível reconhecer a UF a partir do número do CAR.'
        ),
    }


def get_or_create_state(uf):
    uf = normalize_uf(uf)
    if not uf:
        return None
    obj, _ = SicarEstado.objects.get_or_create(uf=uf)
    return obj


def mark_state_processing(uf, lote):
    state = get_or_create_state(uf)
    if not state:
        return
    state.status = SicarEstado.Status.PROCESSANDO
    state.ultimo_lote = lote
    state.save(update_fields=['status', 'ultimo_lote', 'atualizado_em'])


def get_fingerprint(uf, dataset_slug):
    return SicarFingerprintCamada.objects.filter(uf=normalize_uf(uf), dataset_slug=dataset_slug).first()


def record_fingerprint(uf, dataset_slug, content_hash, file_hash, importacao, changed):
    now = timezone.now()
    obj, _ = SicarFingerprintCamada.objects.get_or_create(
        uf=normalize_uf(uf), dataset_slug=dataset_slug,
        defaults={
            'hash_conteudo': content_hash,
            'hash_arquivo': file_hash or '',
            'ultima_verificacao': now,
            'ultima_atualizacao': now if changed else None,
            'ultima_importacao': importacao,
        },
    )
    obj.hash_conteudo = content_hash
    obj.hash_arquivo = file_hash or ''
    obj.ultima_verificacao = now
    obj.ultima_importacao = importacao
    if changed:
        obj.ultima_atualizacao = getattr(importacao, 'data_finalizacao', None) or now
    obj.save()
    return obj


def state_rows():
    stored = {obj.uf: obj for obj in SicarEstado.objects.all()}
    expected = list(datasets_for_source('sicar'))
    expected_slugs = {spec.slug for spec in expected}
    fingerprints = {}
    for fp in SicarFingerprintCamada.objects.filter(dataset_slug__in=expected_slugs).order_by('uf', 'dataset_slug'):
        fingerprints.setdefault(fp.uf, []).append(fp)

    rows = []
    for uf in sorted(UF_CODES):
        obj = stored.get(uf)
        known = fingerprints.get(uf, [])
        rows.append({
            'uf': uf,
            'nome': UF_NAMES.get(uf, uf),
            'obj': obj,
            'status': obj.status if obj else SicarEstado.Status.NUNCA_IMPORTADO,
            'status_label': obj.get_status_display() if obj else 'Nunca importado',
            'ultima_verificacao': obj.ultima_verificacao if obj else None,
            'ultima_atualizacao': obj.ultima_atualizacao if obj else None,
            'detalhes': obj.detalhes if obj else {},
            'camadas_registradas': len(known),
            'camadas_total': len(expected),
            'camadas_faltantes': max(0, len(expected) - len(known)),
        })
    return rows
