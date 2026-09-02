import re
import unicodedata
from difflib import SequenceMatcher


def norm(value):
    value = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def geometry_family(value):
    v = norm(value)
    if 'polygon' in v:
        return 'polygon'
    if 'line' in v or 'curve' in v:
        return 'line'
    if 'point' in v:
        return 'point'
    return v


def dtype_family(value, ogr_type=None):
    text = norm(ogr_type or value)
    if any(token in text for token in ('real', 'float', 'double', 'numeric', 'decimal')):
        return 'real'
    if any(token in text for token in ('integer', 'int8', 'int16', 'int32', 'int64', 'long')):
        return 'integer'
    if 'bool' in text:
        return 'boolean'
    if 'datetime' in text or 'timestamp' in text:
        return 'datetime'
    if text == 'date' or text.startswith('date_'):
        return 'date'
    if 'time' in text:
        return 'time'
    if any(token in text for token in ('string', 'text', 'object', 'char')):
        return 'text'
    return text or 'unknown'


def field_definitions(layer):
    definitions = layer.get('field_definitions') or []
    if definitions:
        result = []
        for index, item in enumerate(definitions):
            result.append({
                'name': str(item.get('name') or ''),
                'normalized': norm(item.get('name')),
                'dtype': str(item.get('dtype') or item.get('type') or ''),
                'ogr_type': str(item.get('ogr_type') or item.get('type') or ''),
                'width': item.get('width'),
                'precision': item.get('precision'),
                'position': int(item.get('position', index)),
            })
        return result

    fields = list(layer.get('fields') or [])
    dtypes = list(layer.get('dtypes') or [])
    return [
        {
            'name': str(name),
            'normalized': norm(name),
            'dtype': str(dtypes[index]) if index < len(dtypes) else '',
            'ogr_type': '',
            'width': None,
            'precision': None,
            'position': index,
        }
        for index, name in enumerate(fields)
    ]


def _dictionary_snapshot(layer):
    metadata = layer.get('sicar_dictionary') or {}
    if not metadata.get('present'):
        return {}
    comparison = metadata.get('comparison') or {}
    return {
        'present': True,
        'table_name': metadata.get('table_name'),
        'columns': list(metadata.get('columns') or []),
        'row_count': int(metadata.get('row_count') or 0),
        'field_catalog': [
            {
                'name': item.get('name'),
                'type': item.get('type'),
                'description': item.get('description'),
                'character_set': item.get('character_set'),
                'srid': item.get('srid'),
                'created_date': item.get('created_date'),
            }
            for item in metadata.get('field_catalog') or []
        ],
        'unknown_dictionary_columns': list(metadata.get('unknown_dictionary_columns') or []),
        'comparison': {
            'dictionary_srids': list(comparison.get('dictionary_srids') or []),
            'actual_epsg': comparison.get('actual_epsg'),
            'crs_divergence': bool(comparison.get('crs_divergence')),
            'character_sets': list(comparison.get('character_sets') or []),
            'created_dates': list(comparison.get('created_dates') or []),
            'missing_in_layer': list(comparison.get('missing_in_layer') or []),
            'undocumented_in_dictionary': list(comparison.get('undocumented_in_dictionary') or []),
            'type_differences': list(comparison.get('type_differences') or []),
            'consistent_fields': bool(comparison.get('consistent_fields')),
        },
    }


def snapshot_layer(layer):
    return {
        'layer_name': str(layer.get('layer_name') or ''),
        'dataset_name': str(layer.get('dataset_name') or ''),
        'fields': field_definitions(layer),
        'geometry_type': str(layer.get('geometry_type') or ''),
        'geometry_family': geometry_family(layer.get('geometry_type')),
        'crs': str(layer.get('crs') or ''),
        'epsg': layer.get('epsg_detectado'),
        'signature': str(layer.get('signature') or ''),
        'sicar_dictionary': _dictionary_snapshot(layer),
    }


def _field_map(snapshot):
    return {item['normalized']: item for item in snapshot.get('fields') or [] if item.get('normalized')}


def _source_for_aliases(snapshot, aliases):
    mapping = _field_map(snapshot)
    for alias in aliases:
        item = mapping.get(norm(alias))
        if item:
            return item
    return None


def _compatible_types(a, b):
    fa = dtype_family(a.get('dtype'), a.get('ogr_type'))
    fb = dtype_family(b.get('dtype'), b.get('ogr_type'))
    if fa == fb:
        return True
    return {fa, fb} <= {'integer', 'real'}


def _precision_changed(old, new):
    return old.get('width') != new.get('width') or old.get('precision') != new.get('precision')



def _norm_dict_item(value):
    return norm(value)


def _compare_source_metadata(previous, current):
    source_metadata_changes = []
    source_metadata_warnings = []
    old_dict = (previous or {}).get('sicar_dictionary') or {}
    new_dict = (current or {}).get('sicar_dictionary') or {}
    if old_dict.get('present') and new_dict.get('present'):
        old_catalog = {_norm_dict_item(item.get('name')): item for item in old_dict.get('field_catalog') or [] if item.get('name')}
        new_catalog = {_norm_dict_item(item.get('name')): item for item in new_dict.get('field_catalog') or [] if item.get('name')}
        for key in sorted(set(old_catalog) - set(new_catalog)):
            source_metadata_changes.append({'type': 'DICTIONARY_FIELD_REMOVED', 'field': old_catalog[key].get('name')})
        for key in sorted(set(new_catalog) - set(old_catalog)):
            source_metadata_changes.append({'type': 'DICTIONARY_FIELD_ADDED', 'field': new_catalog[key].get('name')})
        for key in sorted(set(old_catalog) & set(new_catalog)):
            old_item, new_item = old_catalog[key], new_catalog[key]
            if norm(old_item.get('type')) != norm(new_item.get('type')):
                source_metadata_changes.append({
                    'type': 'DICTIONARY_TYPE_CHANGED', 'field': new_item.get('name'),
                    'from': old_item.get('type'), 'to': new_item.get('type'),
                })
            if str(old_item.get('description') or '') != str(new_item.get('description') or ''):
                source_metadata_changes.append({
                    'type': 'DICTIONARY_DESCRIPTION_CHANGED', 'field': new_item.get('name'),
                    'from': old_item.get('description'), 'to': new_item.get('description'),
                })
        old_cmp = old_dict.get('comparison') or {}
        new_cmp = new_dict.get('comparison') or {}
        if list(old_cmp.get('dictionary_srids') or []) != list(new_cmp.get('dictionary_srids') or []):
            source_metadata_changes.append({
                'type': 'DICTIONARY_SRID_CHANGED',
                'from': old_cmp.get('dictionary_srids') or [],
                'to': new_cmp.get('dictionary_srids') or [],
            })
        if list(old_cmp.get('character_sets') or []) != list(new_cmp.get('character_sets') or []):
            source_metadata_changes.append({
                'type': 'DICTIONARY_CHARSET_CHANGED',
                'from': old_cmp.get('character_sets') or [],
                'to': new_cmp.get('character_sets') or [],
            })
    if new_dict.get('present'):
        cmp = new_dict.get('comparison') or {}
        if cmp.get('crs_divergence'):
            source_metadata_warnings.append({
                'type': 'DICTIONARY_CRS_DIVERGENCE',
                'actual_epsg': cmp.get('actual_epsg'),
                'dictionary_srids': cmp.get('dictionary_srids') or [],
                'policy': 'CRS espacial efetivo tem prioridade; DICIONARIO permanece como metadado auxiliar.',
            })
        if cmp.get('missing_in_layer'):
            source_metadata_warnings.append({'type': 'DICTIONARY_FIELDS_MISSING_IN_LAYER', 'fields': cmp.get('missing_in_layer')})
        if cmp.get('undocumented_in_dictionary'):
            source_metadata_warnings.append({'type': 'LAYER_FIELDS_NOT_DOCUMENTED', 'fields': cmp.get('undocumented_in_dictionary')})
        if cmp.get('type_differences'):
            source_metadata_warnings.append({'type': 'DICTIONARY_TYPE_DIVERGENCES', 'fields': cmp.get('type_differences')})
    return source_metadata_changes, source_metadata_warnings

def compare_schema(previous, current, spec=None):
    if not previous:
        source_metadata_changes, source_metadata_warnings = _compare_source_metadata({}, current)
        return {
            'baseline': True,
            'changed': False,
            'severity': 'BASELINE',
            'changes': [],
            'summary': 'Primeira estrutura detalhada registrada para comparação futura.',
            'source_metadata_changes': source_metadata_changes,
            'source_metadata_warnings': source_metadata_warnings,
        }

    changes = []
    old_map = _field_map(previous)
    new_map = _field_map(current)
    old_keys = set(old_map)
    new_keys = set(new_map)

    if previous.get('layer_name') and previous.get('layer_name') != current.get('layer_name'):
        changes.append({
            'type': 'LAYER_RENAMED',
            'severity': 'WARNING',
            'from': previous.get('layer_name'),
            'to': current.get('layer_name'),
        })

    if previous.get('dataset_name') and previous.get('dataset_name') != current.get('dataset_name'):
        changes.append({
            'type': 'DATASET_FILE_RENAMED',
            'severity': 'INFO',
            'from': previous.get('dataset_name'),
            'to': current.get('dataset_name'),
        })

    has_field_baseline = bool(previous.get('fields'))
    common = sorted(old_keys & new_keys) if has_field_baseline else []
    for key in common:
        old = old_map[key]
        new = new_map[key]
        if old.get('name') != new.get('name'):
            changes.append({
                'type': 'FIELD_SPELLING_CHANGED',
                'severity': 'INFO',
                'field': key,
                'from': old.get('name'),
                'to': new.get('name'),
            })
        old_family = dtype_family(old.get('dtype'), old.get('ogr_type'))
        new_family = dtype_family(new.get('dtype'), new.get('ogr_type'))
        if old_family != new_family:
            changes.append({
                'type': 'FIELD_TYPE_CHANGED',
                'severity': 'WARNING',
                'field': new.get('name'),
                'from': old.get('ogr_type') or old.get('dtype'),
                'to': new.get('ogr_type') or new.get('dtype'),
            })
        elif _precision_changed(old, new) and (old_family in {'integer', 'real'} or new_family in {'integer', 'real'}):
            changes.append({
                'type': 'NUMERIC_PRECISION_CHANGED',
                'severity': 'WARNING',
                'field': new.get('name'),
                'from': {'width': old.get('width'), 'precision': old.get('precision')},
                'to': {'width': new.get('width'), 'precision': new.get('precision')},
            })

    removed = [old_map[k] for k in sorted(old_keys - new_keys)] if has_field_baseline else []
    added = [new_map[k] for k in sorted(new_keys - old_keys)] if has_field_baseline else []
    renamed_pairs = []
    used_old = set()
    used_new = set()

    # Primeiro usa o contrato lógico do CONFRONTA: aliases conhecidos do mesmo campo.
    if spec is not None:
        for logical in spec.fields:
            old = _source_for_aliases(previous, logical.aliases)
            new = _source_for_aliases(current, logical.aliases)
            if old and new and old['normalized'] != new['normalized']:
                pair = {
                    'type': 'FIELD_RENAMED',
                    'severity': 'WARNING',
                    'logical_field': logical.canonical,
                    'from': old.get('name'),
                    'to': new.get('name'),
                    'confidence': 'HIGH',
                }
                if pair not in renamed_pairs:
                    renamed_pairs.append(pair)
                used_old.add(old['normalized'])
                used_new.add(new['normalized'])

    # Depois sugere renomeações não cadastradas; apenas informa, nunca cria alias sozinho.
    for old in removed:
        if old['normalized'] in used_old:
            continue
        candidates = []
        for new in added:
            if new['normalized'] in used_new or not _compatible_types(old, new):
                continue
            name_ratio = SequenceMatcher(None, old['normalized'], new['normalized']).ratio()
            position_gap = abs(int(old.get('position', 0)) - int(new.get('position', 0)))
            position_score = 1.0 if position_gap == 0 else 0.7 if position_gap == 1 else 0.0
            score = (name_ratio * 0.75) + (position_score * 0.25)
            if score >= 0.62:
                candidates.append((score, new))
        if candidates:
            score, new = max(candidates, key=lambda item: item[0])
            renamed_pairs.append({
                'type': 'POSSIBLE_FIELD_RENAMED',
                'severity': 'WARNING',
                'from': old.get('name'),
                'to': new.get('name'),
                'confidence': 'MEDIUM' if score >= 0.75 else 'LOW',
            })
            used_old.add(old['normalized'])
            used_new.add(new['normalized'])

    changes.extend(renamed_pairs)

    for old in removed:
        if old['normalized'] not in used_old:
            changes.append({'type': 'FIELD_REMOVED', 'severity': 'WARNING', 'field': old.get('name')})
    for new in added:
        if new['normalized'] not in used_new:
            changes.append({'type': 'FIELD_ADDED', 'severity': 'INFO', 'field': new.get('name')})

    old_geom = previous.get('geometry_type') or ''
    new_geom = current.get('geometry_type') or ''
    if old_geom and new_geom and norm(old_geom) != norm(new_geom):
        same_family = geometry_family(old_geom) == geometry_family(new_geom)
        changes.append({
            'type': 'GEOMETRY_TYPE_CHANGED',
            'severity': 'WARNING' if same_family else 'CRITICAL',
            'from': old_geom,
            'to': new_geom,
            'same_family': same_family,
        })

    old_epsg = previous.get('epsg')
    new_epsg = current.get('epsg')
    if old_epsg and new_epsg and int(old_epsg) != int(new_epsg):
        changes.append({
            'type': 'CRS_CHANGED',
            'severity': 'WARNING',
            'from': old_epsg,
            'to': new_epsg,
        })
    elif not old_epsg and previous.get('crs') and current.get('crs') and previous.get('crs') != current.get('crs'):
        changes.append({
            'type': 'CRS_CHANGED',
            'severity': 'WARNING',
            'from': previous.get('crs'),
            'to': current.get('crs'),
        })

    source_metadata_changes, source_metadata_warnings = _compare_source_metadata(previous, current)

    levels = {'INFO': 1, 'WARNING': 2, 'CRITICAL': 3}
    severity = max((item['severity'] for item in changes), key=lambda x: levels[x], default='NONE')
    return {
        'baseline': False,
        'changed': bool(changes),
        'severity': severity,
        'changes': changes,
        'source_metadata_changes': source_metadata_changes,
        'source_metadata_warnings': source_metadata_warnings,
        'summary': _summary(changes),
    }


def _summary(changes):
    if not changes:
        return 'Nenhuma alteração estrutural detectada em relação à última estrutura conhecida.'
    labels = {
        'LAYER_RENAMED': 'camada renomeada',
        'DATASET_FILE_RENAMED': 'arquivo interno renomeado',
        'FIELD_SPELLING_CHANGED': 'grafia/capitalização de campo alterada',
        'FIELD_TYPE_CHANGED': 'tipo de campo alterado',
        'NUMERIC_PRECISION_CHANGED': 'precisão numérica alterada',
        'FIELD_RENAMED': 'campo renomeado por alias conhecido',
        'POSSIBLE_FIELD_RENAMED': 'possível renomeação de campo',
        'FIELD_REMOVED': 'campo removido',
        'FIELD_ADDED': 'campo novo',
        'GEOMETRY_TYPE_CHANGED': 'tipo de geometria alterado',
        'CRS_CHANGED': 'CRS/SRID alterado',
    }
    counts = {}
    for item in changes:
        key = labels.get(item.get('type'), item.get('type', 'alteração'))
        counts[key] = counts.get(key, 0) + 1
    return '; '.join(f'{label}: {count}' for label, count in counts.items()) + '.'


def format_alert_message(spec, drift):
    if not drift or not drift.get('changed'):
        return ''
    lines = [
        f'A fonte oficial alterou a estrutura de {spec.label}.',
        drift.get('summary', '').strip(),
        'O CONFRONTA registrou a mudança e aplicou somente adaptações consideradas seguras; revise o relatório da importação para os detalhes.',
    ]
    return ' '.join(line for line in lines if line)
