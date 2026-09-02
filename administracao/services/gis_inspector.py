import hashlib
import json
from pathlib import Path

import pyogrio
from pyproj import CRS

from .exceptions import GISValidationError
from .names import normalize_identifier
from .sicar_gpkg_metadata import enrich_layers_with_sicar_dictionary

VECTOR_DATASET_EXTENSIONS = {'.shp', '.gpkg', '.geojson', '.json', '.gml', '.kml'}

_DBF_TYPE_NAMES = {
    'C': 'String',
    'N': 'Numeric',
    'F': 'Real',
    'D': 'Date',
    'L': 'Boolean',
    'M': 'String',
    'I': 'Integer',
    'B': 'Double',
    'T': 'DateTime',
    'Y': 'Currency',
}


def discover_datasets(extracted_dir):
    root = Path(extracted_dir)
    datasets = []
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.suffix.lower() in VECTOR_DATASET_EXTENSIONS:
            # Arquivos JSON genéricos podem não ser dados vetoriais; a leitura posterior valida.
            datasets.append(path)
    if not datasets:
        raise GISValidationError('Nenhum dataset vetorial suportado foi encontrado no ZIP.')
    return datasets




def _find_shapefile_sidecar(path: Path, suffix: str):
    expected = path.with_suffix(suffix)
    if expected.exists():
        return expected
    for candidate in path.parent.iterdir():
        if candidate.is_file() and candidate.stem.lower() == path.stem.lower() and candidate.suffix.lower() == suffix.lower():
            return candidate
    return None


def _declared_shapefile_encoding(path: Path):
    """Lê a declaração de charset usada por fontes públicas brasileiras.

    O padrão mais comum é .cpg, mas a FUNAI publica atualmente um sidecar
    .cst (ex.: ISO-8859-1). O GDAL nem sempre aplica .cst automaticamente
    quando o arquivo é aberto por outra ferramenta, então o Manage torna a
    declaração explícita e a repassa ao ogr2ogr.
    """
    if path.suffix.lower() != '.shp':
        return ''
    for suffix in ('.cpg', '.cst'):
        sidecar = _find_shapefile_sidecar(path, suffix)
        if not sidecar:
            continue
        try:
            raw = sidecar.read_bytes().strip().strip(b'\x00')
        except OSError:
            continue
        for codec in ('utf-8-sig', 'ascii', 'latin-1'):
            try:
                value = raw.decode(codec).strip().strip('\x00')
            except UnicodeDecodeError:
                continue
            if value:
                normalized = value.upper().replace('_', '-').replace(' ', '')
                aliases = {
                    'UTF8': 'UTF-8',
                    'UTF-8': 'UTF-8',
                    'LATIN1': 'ISO-8859-1',
                    'LATIN-1': 'ISO-8859-1',
                    'ISO8859-1': 'ISO-8859-1',
                    'ISO-8859-1': 'ISO-8859-1',
                    'WINDOWS-1252': 'CP1252',
                    'CP1252': 'CP1252',
                }
                return aliases.get(normalized, value)
    return ''

def _fallback_field_definitions(fields, dtypes):
    return [
        {
            'name': str(name),
            'dtype': str(dtypes[index]) if index < len(dtypes) else '',
            'ogr_type': '',
            'width': None,
            'precision': None,
            'position': index,
        }
        for index, name in enumerate(fields)
    ]


def _dbf_field_definitions(shp_path, fields, dtypes):
    """Lê width/precision diretamente do cabeçalho DBF.

    Isso é importante porque, no Shapefile, OGR width/precision pode gerar no
    PostgreSQL tipos NUMERIC excessivamente restritos (ex.: NUMERIC(33,31)).
    A leitura é apenas de metadados; os dados continuam sendo lidos pelo GDAL.
    """
    shp_path = Path(shp_path)
    if shp_path.suffix.lower() != '.shp':
        return _fallback_field_definitions(fields, dtypes)

    dbf_path = None
    expected = shp_path.with_suffix('.dbf')
    if expected.exists():
        dbf_path = expected
    else:
        # ZIPs oficiais às vezes preservam extensão em caixa alta.
        for candidate in shp_path.parent.iterdir():
            if candidate.is_file() and candidate.stem.lower() == shp_path.stem.lower() and candidate.suffix.lower() == '.dbf':
                dbf_path = candidate
                break
    if not dbf_path:
        return _fallback_field_definitions(fields, dtypes)

    try:
        with dbf_path.open('rb') as fh:
            header = fh.read(32)
            if len(header) < 32:
                return _fallback_field_definitions(fields, dtypes)
            header_length = int.from_bytes(header[8:10], 'little', signed=False)
            descriptors_length = max(0, header_length - 33)
            raw = fh.read(descriptors_length)
    except OSError:
        return _fallback_field_definitions(fields, dtypes)

    parsed = []
    for offset in range(0, len(raw), 32):
        block = raw[offset:offset + 32]
        if len(block) < 32 or block[0] == 0x0D:
            break
        name = block[0:11].split(b'\x00', 1)[0].decode('latin-1', errors='replace').strip()
        dbf_type = chr(block[11]) if block[11] else ''
        width = int(block[16])
        precision = int(block[17])
        ogr_type = _DBF_TYPE_NAMES.get(dbf_type, dbf_type)
        if dbf_type == 'N':
            ogr_type = 'Real' if precision else 'Integer64'
        parsed.append({
            'name': name,
            'dtype': '',
            'ogr_type': ogr_type,
            'width': width or None,
            'precision': precision,
            'position': len(parsed),
        })

    if not parsed:
        return _fallback_field_definitions(fields, dtypes)

    dtype_by_name = {str(name).lower(): str(dtypes[index]) if index < len(dtypes) else '' for index, name in enumerate(fields)}
    for item in parsed:
        item['dtype'] = dtype_by_name.get(item['name'].lower(), '')
    return parsed


def inspect_dataset(path):
    path = Path(path)
    try:
        layers = pyogrio.list_layers(path)
    except Exception as exc:
        raise GISValidationError(f'Não foi possível ler o dataset {path.name}: {exc}') from exc

    result = []
    for row in layers:
        layer_name = str(row[0])
        declared_encoding = _declared_shapefile_encoding(path)
        encoding_override = declared_encoding or None
        try:
            if declared_encoding:
                info = pyogrio.read_info(path, layer=layer_name, encoding=declared_encoding)
            else:
                info = pyogrio.read_info(path, layer=layer_name)
        except UnicodeDecodeError as first_exc:
            # Alguns Shapefiles oficiais antigos não trazem .cpg confiável.
            # O GDAL aceita override explícito de ENCODING; tentamos somente
            # encodings comuns no legado brasileiro e registramos a adaptação.
            if path.suffix.lower() != '.shp':
                raise GISValidationError(f'Falha ao ler metadados de {path.name}/{layer_name}: {first_exc}') from first_exc
            info = None
            last_exc = first_exc
            for candidate_encoding in ('CP1252', 'ISO-8859-1'):
                try:
                    info = pyogrio.read_info(path, layer=layer_name, encoding=candidate_encoding)
                    encoding_override = candidate_encoding
                    break
                except Exception as exc:
                    last_exc = exc
            if info is None:
                raise GISValidationError(f'Falha ao ler metadados de {path.name}/{layer_name}: {last_exc}') from last_exc
        except Exception as exc:
            raise GISValidationError(f'Falha ao ler metadados de {path.name}/{layer_name}: {exc}') from exc

        fields_raw = info.get('fields')
        dtypes_raw = info.get('dtypes')
        fields = [str(v) for v in list(fields_raw)] if fields_raw is not None else []
        dtypes = [str(v) for v in list(dtypes_raw)] if dtypes_raw is not None else []
        field_definitions = _dbf_field_definitions(path, fields, dtypes)
        geometry_type = str(info.get('geometry_type') or '')

        # GeoPackages oficiais podem conter tabelas auxiliares não espaciais
        # (ex.: DICIONARIO) junto da camada GIS principal. Tabela sem geometria
        # não precisa de CRS e não deve derrubar a inspeção do arquivo inteiro.
        # Já uma camada espacial sem CRS continua sendo bloqueada: não inferimos
        # projeção/datum silenciosamente.
        geometry_norm = geometry_type.strip().lower()
        is_spatial = bool(
            geometry_norm
            and geometry_norm not in {'none', 'null', 'non spatial', 'non-spatial'}
        )
        crs_raw = info.get('crs')
        if not crs_raw and is_spatial:
            raise GISValidationError(f'CRS não confirmado para a camada {layer_name} em {path.name}.')

        if crs_raw:
            try:
                crs = CRS.from_user_input(crs_raw)
                epsg = crs.to_epsg()
                crs_canonical = crs.to_string()
            except Exception as exc:
                raise GISValidationError(f'CRS inválido ou não reconhecido na camada {layer_name}: {exc}') from exc
        else:
            epsg = None
            crs_canonical = ''
        feature_count = info.get('features')
        signature_payload = {
            'fields': [
                {
                    'name': item.get('name'),
                    'dtype': item.get('dtype'),
                    'ogr_type': item.get('ogr_type'),
                    'width': item.get('width'),
                    'precision': item.get('precision'),
                }
                for item in field_definitions
            ],
            'geometry_type': geometry_type,
            'crs': crs_canonical,
        }
        signature = hashlib.sha256(
            json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
        ).hexdigest()
        table_name = normalize_identifier(layer_name or path.stem)
        result.append({
            'dataset_path': str(path),
            'dataset_name': path.name,
            'layer_name': layer_name,
            'table_name': table_name,
            'fields': fields,
            'dtypes': dtypes,
            'field_definitions': field_definitions,
            'geometry_type': geometry_type,
            'is_spatial': is_spatial,
            'auxiliary_table': not is_spatial,
            'crs': crs_canonical,
            'epsg_detectado': epsg,
            'feature_count_reported': feature_count,
            'signature': signature,
            'source_encoding': declared_encoding or info.get('encoding') or encoding_override,
            'encoding_override': ((encoding_override or info.get('encoding')) if path.suffix.lower() == '.shp' else None),
            'encoding_sidecar': declared_encoding,
        })
    if path.suffix.lower() == '.gpkg':
        result = enrich_layers_with_sicar_dictionary(path, result)
    return result


def inspect_all(extracted_dir):
    layers = []
    table_names = {}
    for dataset in discover_datasets(extracted_dir):
        for layer in inspect_dataset(dataset):
            name = layer['table_name']
            if name in table_names:
                other = table_names[name]
                raise GISValidationError(
                    f'Colisão de nomes após normalização: {other} e {layer["layer_name"]} resultam em {name}.'
                )
            table_names[name] = layer['layer_name']
            layers.append(layer)
    if not layers:
        raise GISValidationError('Nenhuma camada vetorial válida foi encontrada.')
    return layers
