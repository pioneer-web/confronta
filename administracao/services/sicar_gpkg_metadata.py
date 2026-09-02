import sqlite3
import unicodedata
import re
from pathlib import Path


def _norm(value):
    value = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def _quote_ident(value):
    return '"' + str(value).replace('"', '""') + '"'


def _table_names(connection):
    cur = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return [str(row[0]) for row in cur.fetchall()]


def _find_table(connection, wanted):
    target = _norm(wanted)
    for name in _table_names(connection):
        if _norm(name) == target:
            return name
    return None


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {'bytes_hex': value.hex()}
    return str(value)


def _rows_as_dicts(connection, table_name):
    cur = connection.execute(f'SELECT * FROM {_quote_ident(table_name)}')
    columns = [str(item[0]) for item in cur.description or []]
    return columns, [
        {column: _json_safe(value) for column, value in zip(columns, row)}
        for row in cur.fetchall()
    ]


def _first_value(row, *names):
    normalized = {_norm(key): value for key, value in row.items()}
    for name in names:
        key = _norm(name)
        if key in normalized:
            return normalized[key]
    return None


def _gpkg_geometry_rows(connection):
    table = _find_table(connection, 'gpkg_geometry_columns')
    if not table:
        return []
    try:
        columns, rows = _rows_as_dicts(connection, table)
    except sqlite3.Error:
        return []
    result = []
    for row in rows:
        result.append({
            'table_name': _first_value(row, 'table_name'),
            'column_name': _first_value(row, 'column_name'),
            'geometry_type_name': _first_value(row, 'geometry_type_name'),
            'srs_id': _first_value(row, 'srs_id'),
            'z': _first_value(row, 'z'),
            'm': _first_value(row, 'm'),
        })
    return result


def _gpkg_content_rows(connection):
    table = _find_table(connection, 'gpkg_contents')
    if not table:
        return []
    try:
        _columns, rows = _rows_as_dicts(connection, table)
    except sqlite3.Error:
        return []
    result = []
    for row in rows:
        result.append({
            'table_name': _first_value(row, 'table_name'),
            'data_type': _first_value(row, 'data_type'),
            'identifier': _first_value(row, 'identifier'),
            'description': _first_value(row, 'description'),
            'srs_id': _first_value(row, 'srs_id'),
        })
    return result


def _dictionary_catalog(rows):
    catalog = []
    for row in rows:
        name = _first_value(row, 'name_column', 'column_name', 'nome_coluna')
        if not name:
            # Preservamos a linha em raw_rows, mas não inventamos um nome lógico.
            continue
        catalog.append({
            'name': str(name),
            'type': _first_value(row, 'type_column', 'column_type', 'tipo_coluna'),
            'description': _first_value(row, 'description', 'descricao'),
            'character_set': _first_value(row, 'character_set', 'charset', 'codificacao'),
            'srid': _first_value(row, 'srid', 'srs_id'),
            'created_date': _first_value(row, 'created_date', 'data_criacao'),
        })
    return catalog


def _type_family(value):
    text = _norm(value)
    if any(token in text for token in ('double', 'float', 'real', 'numeric', 'decimal')):
        return 'real'
    if any(token in text for token in ('integer', 'int', 'long')):
        return 'integer'
    if any(token in text for token in ('string', 'text', 'char', 'object')):
        return 'text'
    if 'bool' in text:
        return 'boolean'
    if 'date' in text or 'time' in text:
        return 'datetime'
    if 'polygon' in text:
        return 'polygon'
    if 'line' in text:
        return 'line'
    if 'point' in text:
        return 'point'
    return text or 'unknown'


def _actual_type_map(layer):
    result = {}
    for item in layer.get('field_definitions') or []:
        name = str(item.get('name') or '')
        if not name:
            continue
        result[_norm(name)] = {
            'name': name,
            'type': item.get('ogr_type') or item.get('dtype') or '',
            'family': _type_family(item.get('ogr_type') or item.get('dtype')),
        }
    return result


def _dictionary_comparison(layer, catalog, geometry_rows):
    layer_name = str(layer.get('layer_name') or '')
    actual = _actual_type_map(layer)
    geom = next((row for row in geometry_rows if _norm(row.get('table_name')) == _norm(layer_name)), None)
    geometry_column = str((geom or {}).get('column_name') or '')
    if geometry_column:
        actual[_norm(geometry_column)] = {
            'name': geometry_column,
            'type': layer.get('geometry_type') or (geom or {}).get('geometry_type_name') or '',
            'family': _type_family(layer.get('geometry_type') or (geom or {}).get('geometry_type_name')),
        }

    documented = {_norm(item.get('name')): item for item in catalog if item.get('name')}
    actual_keys = set(actual)
    documented_keys = set(documented)

    internal_names = {'fid', 'ogc_fid', 'id'}
    undocumented = [actual[key]['name'] for key in sorted(actual_keys - documented_keys) if key not in internal_names]
    missing = [documented[key]['name'] for key in sorted(documented_keys - actual_keys)]

    type_differences = []
    for key in sorted(actual_keys & documented_keys):
        declared = documented[key]
        observed = actual[key]
        declared_family = _type_family(declared.get('type'))
        observed_family = observed.get('family')
        # Numeric integer/real is a compatible widening. OGR object/text is also
        # a representation genérica comum para strings do GeoPackage.
        compatible = declared_family == observed_family or {declared_family, observed_family} <= {'integer', 'real'}
        if not compatible and declared_family not in {'unknown', ''} and observed_family not in {'unknown', ''}:
            type_differences.append({
                'field': observed.get('name'),
                'declared': declared.get('type'),
                'observed': observed.get('type'),
            })

    declared_srids = sorted({int(item['srid']) for item in catalog if str(item.get('srid') or '').isdigit()})
    actual_epsg = layer.get('epsg_detectado')
    crs_divergence = bool(actual_epsg and declared_srids and int(actual_epsg) not in declared_srids)
    charsets = sorted({str(item.get('character_set')) for item in catalog if item.get('character_set')})
    created_dates = sorted({str(item.get('created_date')) for item in catalog if item.get('created_date')})

    return {
        'layer_name': layer_name,
        'geometry_column': geometry_column,
        'documented_field_count': len(documented),
        'actual_field_count': len(actual),
        'missing_in_layer': missing,
        'undocumented_in_dictionary': undocumented,
        'type_differences': type_differences,
        'dictionary_srids': declared_srids,
        'actual_epsg': actual_epsg,
        'crs_divergence': crs_divergence,
        'crs_policy': 'CRS da camada espacial/GDAL tem prioridade; SRID do DICIONARIO é metadado auxiliar.',
        'character_sets': charsets,
        'created_dates': created_dates,
        'consistent_fields': not missing and not undocumented and not type_differences,
    }


def enrich_layers_with_sicar_dictionary(path, layers):
    """Lê a tabela DICIONARIO dos GeoPackages SICAR sem tratá-la como GIS.

    O parser é intencionalmente tolerante a colunas adicionais: todas as colunas
    e linhas são preservadas em ``raw_rows`` e os campos conhecidos são extraídos
    apenas quando presentes. Mudanças futuras no layout do DICIONARIO não impedem
    a leitura da camada espacial.
    """
    path = Path(path)
    if path.suffix.lower() != '.gpkg' or not path.exists():
        return layers

    try:
        uri = f'file:{path.as_posix()}?mode=ro'
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return layers

    try:
        dictionary_table = _find_table(connection, 'DICIONARIO')
        if not dictionary_table:
            return layers
        columns, raw_rows = _rows_as_dicts(connection, dictionary_table)
        catalog = _dictionary_catalog(raw_rows)
        geometry_rows = _gpkg_geometry_rows(connection)
        content_rows = _gpkg_content_rows(connection)
    except sqlite3.Error:
        return layers
    finally:
        connection.close()

    base_metadata = {
        'present': True,
        'table_name': dictionary_table,
        'columns': columns,
        'row_count': len(raw_rows),
        'field_catalog': catalog,
        'raw_rows': raw_rows,
        'gpkg_contents': content_rows,
        'gpkg_geometry_columns': geometry_rows,
        'unknown_dictionary_columns': [
            name for name in columns
            if _norm(name) not in {
                'id', 'name_column', 'column_name', 'nome_coluna', 'type_column', 'column_type', 'tipo_coluna',
                'description', 'descricao', 'character_set', 'charset', 'codificacao', 'srid', 'srs_id',
                'created_date', 'data_criacao'
            }
        ],
    }

    spatial_layers = [layer for layer in layers if layer.get('is_spatial')]
    for layer in layers:
        metadata = dict(base_metadata)
        if layer.get('is_spatial'):
            metadata['comparison'] = _dictionary_comparison(layer, catalog, geometry_rows)
        else:
            metadata['comparison'] = {}
        layer['sicar_dictionary'] = metadata

    # Se houver exatamente uma camada espacial, o DICIONARIO é inequivocamente
    # associado a ela. Com várias camadas, cada uma recebe sua comparação própria,
    # sem assumir silenciosamente a qual delas o catálogo pertence.
    association = 'UNICA_CAMADA_ESPACIAL' if len(spatial_layers) == 1 else 'MULTIPLAS_CAMADAS_ESPACIAIS'
    for layer in layers:
        layer['sicar_dictionary']['association'] = association
    return layers


def compact_sicar_dictionary(metadata):
    if not metadata or not metadata.get('present'):
        return {}
    comparison = metadata.get('comparison') or {}
    return {
        'present': True,
        'table_name': metadata.get('table_name'),
        'columns': list(metadata.get('columns') or []),
        'row_count': int(metadata.get('row_count') or 0),
        'field_catalog': list(metadata.get('field_catalog') or []),
        'raw_rows': list(metadata.get('raw_rows') or []),
        'unknown_dictionary_columns': list(metadata.get('unknown_dictionary_columns') or []),
        'association': metadata.get('association'),
        'comparison': comparison,
    }
