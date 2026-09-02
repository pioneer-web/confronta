from __future__ import annotations

import shutil
from pathlib import Path


_UTF8_NAMES = {'utf-8', 'utf8', 'utf_8'}


def _normalize_encoding(value):
    return str(value or '').strip().lower().replace(' ', '')


def _find_sidecar(shp_path: Path, suffix: str):
    expected = shp_path.with_suffix(suffix)
    if expected.exists():
        return expected
    for candidate in shp_path.parent.iterdir():
        if candidate.is_file() and candidate.stem.lower() == shp_path.stem.lower() and candidate.suffix.lower() == suffix.lower():
            return candidate
    return None


def _effective_shapefile_encoding(layer, shp_path: Path):
    declared = layer.get('source_encoding') or layer.get('encoding_override')
    if declared:
        return str(declared).strip()
    # .cpg é o sidecar mais comum, mas algumas bases oficiais brasileiras
    # (incluindo a FUNAI) publicam a declaração de charset em .cst.
    for suffix in ('.cpg', '.cst'):
        charset_path = _find_sidecar(shp_path, suffix)
        if not charset_path:
            continue
        try:
            raw = charset_path.read_bytes().strip()
            for codec in ('utf-8-sig', 'ascii', 'latin-1'):
                try:
                    value = raw.decode(codec).strip().strip('\x00')
                    if value:
                        return value
                except UnicodeDecodeError:
                    continue
        except OSError:
            continue
    return ''


def _dbf_layout(dbf_path: Path):
    with dbf_path.open('rb') as fh:
        header = fh.read(32)
        if len(header) < 32:
            raise ValueError('Cabeçalho DBF incompleto.')
        record_count = int.from_bytes(header[4:8], 'little', signed=False)
        header_length = int.from_bytes(header[8:10], 'little', signed=False)
        record_length = int.from_bytes(header[10:12], 'little', signed=False)
        if header_length < 33 or record_length < 1:
            raise ValueError('Estrutura DBF inválida.')
        descriptor_bytes = fh.read(header_length - 32)

    fields = []
    offset = 1  # byte inicial de exclusão lógica em cada registro
    for pos in range(0, len(descriptor_bytes), 32):
        block = descriptor_bytes[pos:pos + 32]
        if not block or block[0] == 0x0D:
            break
        if len(block) < 32:
            break
        name = block[0:11].split(b'\x00', 1)[0].decode('latin-1', errors='replace').strip()
        field_type = chr(block[11]) if block[11] else ''
        width = int(block[16])
        fields.append({
            'name': name,
            'type': field_type,
            'width': width,
            'offset': offset,
        })
        offset += width

    if offset > record_length:
        raise ValueError('Descritores DBF excedem o tamanho declarado do registro.')
    return {
        'record_count': record_count,
        'header_length': header_length,
        'record_length': record_length,
        'fields': fields,
    }


def _replace_invalid_utf8_bytes(raw: bytes):
    """Substitui somente bytes que impedem UTF-8 válido, preservando o tamanho.

    O DBF usa campos de largura fixa. Não podemos inserir U+FFFD porque sua forma
    UTF-8 ocupa três bytes e poderia deslocar todo o registro. Cada byte inválido
    é substituído por '?' (ASCII, um byte), mantendo exatamente a largura original.
    """
    if not raw:
        return raw, 0
    try:
        raw.decode('utf-8')
        return raw, 0
    except UnicodeDecodeError:
        pass

    remaining = raw
    out = bytearray()
    replacements = 0
    while remaining:
        try:
            remaining.decode('utf-8')
            out.extend(remaining)
            break
        except UnicodeDecodeError as exc:
            out.extend(remaining[:exc.start])
            bad_len = max(1, exc.end - exc.start)
            out.extend(b'?' * bad_len)
            replacements += bad_len
            remaining = remaining[exc.end:]

    # Proteção adicional: o reparo nunca pode alterar a largura fixa do campo.
    if len(out) < len(raw):
        out.extend(b' ' * (len(raw) - len(out)))
    elif len(out) > len(raw):
        out = out[:len(raw)]
    out.decode('utf-8')  # validação final; se falhar, aborta em vez de mascarar.
    return bytes(out), replacements


def inspect_invalid_utf8_dbf(dbf_path: Path):
    """Localiza texto UTF-8 malformado em campos Character sem alterar o arquivo."""
    layout = _dbf_layout(dbf_path)
    raw = dbf_path.read_bytes()
    repairs = []
    total_bytes = 0
    for record_index in range(layout['record_count']):
        base = layout['header_length'] + record_index * layout['record_length']
        if base + layout['record_length'] > len(raw):
            break
        # '*' = registro logicamente excluído no DBF. Não será importado pelo OGR.
        if raw[base:base + 1] == b'*':
            continue
        for field in layout['fields']:
            if field['type'] != 'C' or not field['width']:
                continue
            start = base + field['offset']
            end = start + field['width']
            original = bytes(raw[start:end])
            _fixed, count = _replace_invalid_utf8_bytes(original)
            if count:
                total_bytes += count
                repairs.append({
                    'registro_dbf': record_index + 1,
                    'fid_ogr_estimado': record_index,
                    'campo': field['name'],
                    'bytes_invalidos': count,
                })
    return {
        'necessario': bool(repairs),
        'registros_corrigiveis': len({item['registro_dbf'] for item in repairs}),
        'ocorrencias': len(repairs),
        'bytes_invalidos': total_bytes,
        'detalhes': repairs[:25],
    }


def _sanitize_dbf_in_place(dbf_path: Path):
    layout = _dbf_layout(dbf_path)
    data = bytearray(dbf_path.read_bytes())
    repairs = []
    total_bytes = 0
    touched_records = set()

    for record_index in range(layout['record_count']):
        base = layout['header_length'] + record_index * layout['record_length']
        if base + layout['record_length'] > len(data):
            break
        if data[base:base + 1] == b'*':
            continue
        for field in layout['fields']:
            if field['type'] != 'C' or not field['width']:
                continue
            start = base + field['offset']
            end = start + field['width']
            original = bytes(data[start:end])
            fixed, count = _replace_invalid_utf8_bytes(original)
            if not count:
                continue
            data[start:end] = fixed
            total_bytes += count
            touched_records.add(record_index + 1)
            repairs.append({
                'registro_dbf': record_index + 1,
                'fid_ogr_estimado': record_index,
                'campo': field['name'],
                'bytes_invalidos': count,
            })

    if repairs:
        dbf_path.write_bytes(data)

    return {
        'aplicado': bool(repairs),
        'registros_corrigidos': len(touched_records),
        'ocorrencias': len(repairs),
        'bytes_substituidos': total_bytes,
        'substituicao': '?',
        'detalhes': repairs[:25],
    }


def prepare_utf8_shapefile_for_import(layer, working_root, enabled=False):
    """Cria cópia operacional sanitizada quando um Shapefile UTF-8 está malformado.

    O arquivo oficial recebido não é editado. A correção ocorre em uma cópia
    temporária, usada somente pelo OGR, e fica registrada no relatório da importação.
    A rotina é genérica, mas conservadora: somente atua em Shapefiles declarados
    UTF-8 que efetivamente contenham sequências inválidas em campos Character.
    """
    report = {
        'aplicavel': False,
        'aplicado': False,
        'motivo': '',
        'encoding': '',
    }
    if not enabled:
        report['motivo'] = 'Sanitização não habilitada para este dataset.'
        return report

    shp_path = Path(layer.get('dataset_path') or '')
    if shp_path.suffix.lower() != '.shp' or not shp_path.exists():
        report['motivo'] = 'Camada não é Shapefile; nenhuma adaptação de DBF foi necessária.'
        return report

    declared_encoding = _effective_shapefile_encoding(layer, shp_path)
    report['encoding'] = declared_encoding
    encoding = _normalize_encoding(declared_encoding)
    if encoding not in _UTF8_NAMES:
        report['motivo'] = f'Encoding declarado não é UTF-8 ({encoding or "não informado"}); arquivo preservado.'
        return report

    dbf_path = _find_sidecar(shp_path, '.dbf')
    if not dbf_path:
        report['motivo'] = 'DBF correspondente não foi encontrado.'
        return report

    report['aplicavel'] = True
    scan = inspect_invalid_utf8_dbf(dbf_path)
    report['analise'] = scan
    if not scan['necessario']:
        report['motivo'] = 'Nenhuma sequência UTF-8 inválida foi encontrada no DBF.'
        return report

    work_dir = Path(working_root) / '_sanitized' / shp_path.stem
    work_dir.mkdir(parents=True, exist_ok=True)
    copied_shp = None
    for candidate in shp_path.parent.iterdir():
        if candidate.is_file() and candidate.stem.lower() == shp_path.stem.lower():
            target = work_dir / candidate.name
            shutil.copy2(candidate, target)
            if candidate.suffix.lower() == '.shp':
                copied_shp = target

    if not copied_shp:
        raise ValueError('Não foi possível preparar a cópia operacional do Shapefile.')
    copied_dbf = _find_sidecar(copied_shp, '.dbf')
    if not copied_dbf:
        raise ValueError('Cópia operacional do DBF não foi criada.')

    applied = _sanitize_dbf_in_place(copied_dbf)
    # A cópia precisa ser integralmente decodificável depois do reparo.
    verification = inspect_invalid_utf8_dbf(copied_dbf)
    if verification['necessario']:
        raise ValueError('A sanitização UTF-8 do DBF não conseguiu produzir uma cópia válida.')

    layer['dataset_path_original'] = str(shp_path)
    layer['dataset_path'] = str(copied_shp)
    # A cópia sanitizada é UTF-8 por definição; não mantenha um fallback
    # CP1252/Latin-1 eventualmente usado apenas para conseguir inspecionar o
    # arquivo original antes do reparo.
    layer['encoding_override'] = 'UTF-8'
    layer['source_encoding'] = 'UTF-8'
    report.update(applied)
    report['arquivo_original_preservado_durante_processamento'] = True
    report['copia_operacional'] = copied_shp.name
    report['motivo'] = (
        'Foram encontradas sequências UTF-8 inválidas em campos texto do DBF. '
        'Somente a cópia operacional foi reparada; nenhuma feição foi descartada.'
    )
    return report
