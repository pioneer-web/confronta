from __future__ import annotations

import csv
import gzip
import hashlib
import io
import logging
import re
import shutil
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from psycopg import sql
from shapely import force_2d, wkt as shapely_wkt
from shapely.geometry import MultiPolygon, Polygon
from shapely.validation import make_valid

from administracao.constants import FONTE_SCHEMAS
from administracao.models import CamadaImportada, Importacao
from .auditoria import registrar_auditoria
from .exceptions import BatchInterruptionRequested, DatasetIdentityError, SecurityValidationError
from .names import normalize_identifier
from .postgis import create_staging_schema, drop_schema, table_exists
from .zip_security import run_antivirus

logger = logging.getLogger(__name__)

_GZIP_MAGIC = b'\x1f\x8b'
_YEAR_RE = re.compile(r'(?<!\d)(20\d{2})(?!\d)')
_SUPPORTED_ENCODINGS = ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1')
_DELIMITERS = (';', ',', '\t', '|')


def _relation(schema: str, table: str):
    return sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))


def _progress(callback, percent, stage):
    if callback:
        try:
            callback(percent, stage)
        except BatchInterruptionRequested:
            raise
        except Exception:
            logger.exception('Falha ao publicar progresso do SICOR.')


def _save_upload(uploaded_file, import_id: int):
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in {'.gz', '.csv'}:
        suffix = '.bin'
    target = Path(settings.QUARANTINE_DIR) / f'sicor_{import_id}{suffix}'
    digest = hashlib.sha256()
    size = 0
    with target.open('wb') as dst:
        for chunk in uploaded_file.chunks():
            size += len(chunk)
            digest.update(chunk)
            dst.write(chunk)
        dst.flush()
    return target, digest.hexdigest(), size


def _prepare_csv(source: Path, import_id: int):
    suffix = source.suffix.lower()
    workdir = Path(settings.EXTRACTED_DIR) / f'sicor_{import_id}'
    workdir.mkdir(parents=True, exist_ok=True)
    csv_path = workdir / 'source.csv'

    if suffix == '.csv':
        # Não duplica CSV grande desnecessariamente; o arquivo de quarentena já é a
        # cópia operacional protegida do upload.
        return source, workdir, {'formato': 'CSV', 'descompactado_bytes': source.stat().st_size}

    if suffix != '.gz':
        raise SecurityValidationError('O SICOR aceita somente arquivos .gz ou .csv nesta versão.')

    with source.open('rb') as fh:
        if fh.read(2) != _GZIP_MAGIC:
            raise SecurityValidationError('O arquivo .gz não possui assinatura GZIP válida.')

    expanded = 0
    limit = int(getattr(settings, 'MAX_ZIP_UNCOMPRESSED_BYTES', 0) or 0)
    try:
        with gzip.open(source, 'rb') as src, csv_path.open('wb') as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                expanded += len(chunk)
                if limit and expanded > limit:
                    raise SecurityValidationError('O CSV descompactado do SICOR excede o limite configurado.')
                dst.write(chunk)
    except (OSError, EOFError) as exc:
        raise SecurityValidationError(f'O arquivo GZIP do SICOR está corrompido ou incompleto: {exc}') from exc

    if expanded == 0:
        raise SecurityValidationError('O arquivo GZIP do SICOR está vazio.')
    compressed = max(1, source.stat().st_size)
    ratio = expanded / compressed
    max_ratio = int(getattr(settings, 'MAX_ZIP_EXPANSION_RATIO', 0) or 0)
    if max_ratio and ratio > max_ratio:
        raise SecurityValidationError('O GZIP do SICOR possui taxa de expansão acima do limite configurado.')
    return csv_path, workdir, {
        'formato': 'GZIP/CSV', 'descompactado_bytes': expanded,
        'arquivo_comprimido_bytes': compressed, 'taxa_expansao': round(ratio, 2),
    }


def _sample_bytes(path: Path, size: int = 256 * 1024) -> bytes:
    with path.open('rb') as fh:
        return fh.read(size)


def _decode_sample(sample: bytes):
    for encoding in _SUPPORTED_ENCODINGS:
        try:
            return sample.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    # latin-1 tecnicamente sempre decodifica; fallback defensivo.
    return sample.decode('utf-8', errors='replace'), 'utf-8-replace'


def _normalized_header(value: str) -> str:
    return normalize_identifier(str(value or '').strip(), prefix='campo')


def _dedupe_headers(headers):
    result = []
    # Reservados pelo próprio importador; se o Banco Central criar uma coluna
    # homônima, ela é preservada com prefixo em vez de colidir silenciosamente.
    used = {'_id', '_numero_linha', '_ano_arquivo', '_arquivo_origem', 'geom'}
    for idx, value in enumerate(headers, start=1):
        base = _normalized_header(value) or f'campo_{idx}'
        name = base
        counter = 2
        while name in used:
            suffix = f'_{counter}'
            name = f'{base[:63-len(suffix)]}{suffix}'
            counter += 1
        used.add(name)
        result.append(name)
    return result


def _expected_aliases(spec):
    aliases = set()
    for field in spec.fields:
        aliases.add(_normalized_header(field.canonical))
        aliases.update(_normalized_header(alias) for alias in field.aliases)
    return aliases


def _delimiter_score(text: str, delimiter: str, expected: set[str]):
    try:
        row = next(csv.reader(io.StringIO(text), delimiter=delimiter))
    except Exception:
        return (-1, [])
    normalized = _dedupe_headers(row)
    matches = sum(1 for h in normalized if h in expected)
    return (matches * 100 + len(normalized), row)


def _detect_csv_format(path: Path, spec):
    sample = _sample_bytes(path)
    text, encoding = _decode_sample(sample)
    expected = _expected_aliases(spec)
    scored = []
    for delimiter in _DELIMITERS:
        score, row = _delimiter_score(text, delimiter, expected)
        scored.append((score, delimiter, row))
    scored.sort(reverse=True, key=lambda x: x[0])
    score, delimiter, header = scored[0]
    if not header or len(header) < 2:
        raise DatasetIdentityError(
            'Não foi possível identificar o cabeçalho do CSV SICOR.',
            {'status': 'NAO_CONFIRMADO', 'motivo': 'Cabeçalho CSV não reconhecido.'},
        )
    normalized = _dedupe_headers(header)
    return {
        'encoding': encoding,
        'delimiter': delimiter,
        'original_headers': [str(v or '').strip() for v in header],
        'headers': normalized,
        'score': score,
    }


def _field_mapping(spec, headers):
    header_set = set(headers)
    mapping = {}
    missing_required = []
    for field in spec.fields:
        candidates = []
        for alias in (field.canonical, *field.aliases):
            normalized = _normalized_header(alias)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        actual = next((candidate for candidate in candidates if candidate in header_set), None)
        mapping[field.canonical] = actual
        if field.required and not actual:
            missing_required.append(field.canonical)
    return mapping, missing_required


def _validate_identity(spec, file_name: str, csv_info):
    mapping, missing = _field_mapping(spec, csv_info['headers'])
    header_set = set(csv_info['headers'])
    missing_identity_groups = []
    for group in tuple(spec.identity_required or ()):
        options = [_normalized_header(value) for value in group if _normalized_header(value)]
        if options and not any(option in header_set for option in options):
            missing_identity_groups.append(list(group))
    if missing or missing_identity_groups:
        report = {
            'status': 'NAO_CONFIRMADO',
            'dataset': spec.slug,
            'arquivo': file_name,
            'formato': 'CSV/GZIP',
            'campos_recebidos': csv_info['headers'],
            'campos_obrigatorios_ausentes': missing,
            'grupos_de_identidade_ausentes': missing_identity_groups,
            'mapeamento': mapping,
        }
        details = []
        if missing:
            details.append('campos obrigatórios: ' + ', '.join(missing))
        if missing_identity_groups:
            details.append('sinais de identidade: ' + '; '.join('/'.join(group) for group in missing_identity_groups))
        raise DatasetIdentityError(
            'O arquivo não confirma o perfil SICOR selecionado (' + ' | '.join(details) + ').',
            report,
        )

    normalized_name = _normalized_header(Path(file_name).stem)
    filename_match = any(_normalized_header(token) in normalized_name for token in spec.filename_patterns)
    known_headers = {v for v in mapping.values() if v}
    extras = [h for h in csv_info['headers'] if h not in known_headers]
    return {
        'status': 'CONFIRMADO',
        'dataset': spec.slug,
        'arquivo': file_name,
        'formato': 'CSV/GZIP',
        'encoding': csv_info['encoding'],
        'delimitador': repr(csv_info['delimiter']),
        'campos_recebidos': csv_info['headers'],
        'mapeamento': mapping,
        'campos_extras_preservados_raw': extras,
        'nome_arquivo_compativel': filename_match,
        'motivo': (
            'Identidade confirmada pelos campos oficiais esperados. O nome do arquivo foi usado apenas como sinal auxiliar.'
        ),
    }


def _extract_year(file_name: str):
    years = [int(match.group(1)) for match in _YEAR_RE.finditer(str(file_name or ''))]
    years = [year for year in years if 2013 <= year <= timezone.localdate().year + 1]
    return years[-1] if years else None


def _safe_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text != '' else None


def _coerce(value, sql_type: str):
    text = _safe_text(value)
    if text is None:
        return None, False
    sql_type = str(sql_type or 'text').lower()
    if sql_type == 'text':
        return text, False
    if sql_type == 'integer':
        try:
            decimal = Decimal(text.replace(' ', '').replace(',', '.'))
            if decimal != decimal.to_integral_value():
                raise InvalidOperation
            return str(int(decimal)), False
        except (InvalidOperation, ValueError):
            return None, True
    if sql_type == 'numeric':
        try:
            normalized = text.replace(' ', '')
            # BCB costuma publicar CSV delimitado por ';'. Aceitamos decimal com
            # vírgula sem alterar milhares de forma silenciosa.
            if ',' in normalized and '.' not in normalized:
                normalized = normalized.replace(',', '.')
            return format(Decimal(normalized), 'f'), False
        except (InvalidOperation, ValueError):
            return None, True
    if sql_type == 'date':
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y%m%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(text[:10], fmt).date().isoformat(), False
            except ValueError:
                continue
        return None, True
    return text, False


def _polygonal_geometry(wkt_value):
    text = _safe_text(wkt_value)
    if not text:
        return None, 'WKT vazio', False
    try:
        geom = shapely_wkt.loads(text)
    except Exception as exc:
        return None, f'WKT inválido: {exc}', False

    repaired = False
    if geom.is_empty:
        return None, 'Geometria vazia', False
    if not geom.is_valid:
        try:
            geom = make_valid(geom)
            repaired = True
        except Exception as exc:
            return None, f'Falha no reparo geométrico: {exc}', False

    if isinstance(geom, Polygon):
        geom = MultiPolygon([geom])
    elif isinstance(geom, MultiPolygon):
        pass
    elif hasattr(geom, 'geoms'):
        polygons = []
        for child in geom.geoms:
            if isinstance(child, Polygon):
                polygons.append(child)
            elif isinstance(child, MultiPolygon):
                polygons.extend(list(child.geoms))
        if polygons:
            geom = MultiPolygon(polygons)
        else:
            return None, f'Geometria não poligonal após validação: {geom.geom_type}', repaired
    else:
        return None, f'Geometria não poligonal: {geom.geom_type}', repaired

    if geom.is_empty or not geom.is_valid:
        return None, 'Geometria permaneceu inválida/vazia após reparo', repaired
    geom = force_2d(geom)

    minx, miny, maxx, maxy = geom.bounds
    if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
        return None, 'Coordenadas fora dos limites geográficos válidos', repaired
    return geom.wkt, '', repaired


def _open_dict_reader(path: Path, csv_info):
    csv.field_size_limit(max(csv.field_size_limit(), 16 * 1024 * 1024))
    handle = path.open('r', encoding=csv_info['encoding'], newline='', errors='strict')
    reader = csv.reader(handle, delimiter=csv_info['delimiter'])
    original = next(reader, None)
    if original is None:
        handle.close()
        raise DatasetIdentityError('O CSV SICOR está vazio.', {'status': 'NAO_CONFIRMADO'})
    headers = _dedupe_headers(original)
    return handle, reader, headers


def _previous_headers(spec, import_id: int):
    for candidate in (
        Importacao.objects.filter(dataset_slug=spec.slug, status=Importacao.Status.CONCLUIDO)
        .exclude(pk=import_id).order_by('-data_inicio')[:20]
    ):
        result = candidate.resultado or {}
        headers = ((result.get('sicor_csv') or {}).get('campos_recebidos')
                   or (result.get('alteracoes_estrutura') or {}).get('campos_recebidos'))
        if headers:
            return list(headers), candidate.pk
    return [], None


def _fingerprint_previous(spec, import_id: int, fingerprint: str, year: int | None):
    existing = CamadaImportada.objects.filter(
        fonte=spec.fonte, dataset_slug=spec.slug,
        schema_banco=FONTE_SCHEMAS[spec.fonte], nome_tabela=spec.stable_table,
        status=CamadaImportada.Status.ATIVA,
    ).first()
    if not existing:
        return None
    for candidate in (
        Importacao.objects.filter(dataset_slug=spec.slug, status=Importacao.Status.CONCLUIDO)
        .exclude(pk=import_id).order_by('-data_inicio')[:30]
    ):
        result = candidate.resultado or {}
        fp = result.get('fingerprint_conteudo') or {}
        previous = fp.get('sha256') if isinstance(fp, dict) else str(fp or '')
        if previous != fingerprint:
            continue
        previous_year = (result.get('sicor_csv') or {}).get('ano_arquivo')
        if spec.year_partitioned and previous_year != year:
            continue
        return candidate
    return None


def _create_staging_tables(staging, raw_columns, spec):
    """Cria staging efêmero sem WAL pesado.

    As tabelas de staging nunca são a base ativa e são descartadas ao final.
    UNLOGGED reduz I/O/WAL sobretudo nos arquivos SICOR multimilionários sem
    reduzir a durabilidade das tabelas publicadas em dados_sicor.
    """
    raw_table = 'sicor_raw_incoming'
    op_table = 'sicor_operational_incoming'
    with connection.cursor() as cursor:
        raw_defs = [
            sql.SQL('_numero_linha bigint NOT NULL'),
            sql.SQL('_ano_arquivo integer'),
            sql.SQL('_arquivo_origem text NOT NULL'),
        ] + [sql.SQL('{} text').format(sql.Identifier(column)) for column in raw_columns]
        cursor.execute(
            sql.SQL('CREATE UNLOGGED TABLE {} ({})').format(
                _relation(staging, raw_table), sql.SQL(', ').join(raw_defs)
            )
        )

        op_defs = [sql.SQL('_numero_linha bigint NOT NULL'), sql.SQL('_ano_arquivo integer')]
        for field in spec.fields:
            op_defs.append(sql.SQL('{} text').format(sql.Identifier(field.canonical)))
        if spec.data_kind == 'sicor_wkt':
            op_defs.append(sql.SQL('_geom_wkt text'))
        cursor.execute(
            sql.SQL('CREATE UNLOGGED TABLE {} ({})').format(
                _relation(staging, op_table), sql.SQL(', ').join(op_defs)
            )
        )
    return raw_table, op_table


def _copy_batch(copy_sql, rows):
    if not rows:
        return
    # Um COPY por vez. A versão anterior mantinha dois COPYs simultâneos na
    # mesma conexão e ainda tentava atualizar o progresso no meio deles. Em
    # arquivos grandes isso podia deixar o worker sem publicar novos estados.
    with connection.cursor() as cursor:
        with cursor.copy(copy_sql) as copy:
            for row in rows:
                copy.write_row(row)


def _stream_progress_percent(handle, csv_path: Path):
    try:
        total_bytes = max(1, int(csv_path.stat().st_size))
        consumed = int(handle.buffer.tell())
        ratio = max(0.0, min(1.0, consumed / total_bytes))
        return 52 + int(round(ratio * 14))  # 52..66
    except Exception:
        return 58


def _copy_rows(csv_path: Path, csv_info, identity, spec, staging, raw_table, op_table, year, file_name, progress_callback=None):
    mapping = identity['mapeamento']
    raw_headers = csv_info['headers']
    raw_copy_sql = sql.SQL('COPY {} ({}) FROM STDIN').format(
        _relation(staging, raw_table),
        sql.SQL(', ').join([
            sql.Identifier('_numero_linha'), sql.Identifier('_ano_arquivo'), sql.Identifier('_arquivo_origem'),
            *[sql.Identifier(h) for h in raw_headers],
        ]),
    )
    op_columns = ['_numero_linha', '_ano_arquivo', *[f.canonical for f in spec.fields]]
    if spec.data_kind == 'sicor_wkt':
        op_columns.append('_geom_wkt')
    op_copy_sql = sql.SQL('COPY {} ({}) FROM STDIN').format(
        _relation(staging, op_table),
        sql.SQL(', ').join(sql.Identifier(h) for h in op_columns),
    )

    fingerprint = hashlib.sha256()
    fingerprint.update(('\x1f'.join(raw_headers) + '\n').encode('utf-8'))
    total = 0
    operational = 0
    invalid_values = {}
    required_empty = {}
    geometry_pending = 0
    geometry_repaired = 0
    geometry_samples = []

    # WKT pode ter geometrias longas; lotes menores limitam memória. Os demais
    # arquivos usam lote maior para melhor throughput.
    batch_size = 2_000 if spec.data_kind == 'sicor_wkt' else 25_000
    raw_batch = []
    op_batch = []

    handle, reader, actual_headers = _open_dict_reader(csv_path, csv_info)
    if actual_headers != raw_headers:
        handle.close()
        raise DatasetIdentityError('O cabeçalho do CSV mudou entre a inspeção e a leitura.', {'status': 'NAO_CONFIRMADO'})

    def flush_batches():
        nonlocal raw_batch, op_batch
        if not raw_batch:
            return
        _copy_batch(raw_copy_sql, raw_batch)
        _copy_batch(op_copy_sql, op_batch)
        raw_batch = []
        op_batch = []
        percent = _stream_progress_percent(handle, csv_path)
        _progress(
            progress_callback,
            percent,
            f'Lendo SICOR em streaming — {total:,} registros'.replace(',', '.'),
        )

    try:
        for line_number, values in enumerate(reader, start=2):
            total += 1
            if len(values) < len(raw_headers):
                values = list(values) + [''] * (len(raw_headers) - len(values))
            elif len(values) > len(raw_headers):
                raise DatasetIdentityError(
                    f'A linha {line_number} possui {len(values)} colunas, mas o cabeçalho possui {len(raw_headers)}.',
                    {'status': 'NAO_CONFIRMADO', 'linha': line_number, 'campos_cabecalho': len(raw_headers)},
                )
            row = dict(zip(raw_headers, values))
            normalized_values = [str(v or '') for v in values]
            fingerprint.update(('\x1f'.join(normalized_values) + '\n').encode('utf-8'))
            raw_batch.append((line_number, year, file_name, *values))

            canonical = []
            row_has_required_value = True
            for field in spec.fields:
                source_name = mapping.get(field.canonical)
                value = row.get(source_name) if source_name else None
                coerced, invalid = _coerce(value, field.sql_type)
                if invalid:
                    invalid_values[field.canonical] = invalid_values.get(field.canonical, 0) + 1
                if field.required and coerced is None:
                    row_has_required_value = False
                    required_empty[field.canonical] = required_empty.get(field.canonical, 0) + 1
                canonical.append(coerced)

            if spec.data_kind == 'sicor_gleba_points' and row_has_required_value:
                point_values = {field.canonical: canonical[idx] for idx, field in enumerate(spec.fields)}
                try:
                    lat = Decimal(point_values.get('vl_latitude'))
                    lon = Decimal(point_values.get('vl_longitude'))
                    point_index = int(point_values.get('nu_indice_ponto'))
                    altitude_text = point_values.get('cgl_vl_altitude')
                    altitude = Decimal(altitude_text) if altitude_text is not None else None
                except (InvalidOperation, TypeError, ValueError):
                    row_has_required_value = False
                    invalid_values['coordenadas_geodesicas'] = invalid_values.get('coordenadas_geodesicas', 0) + 1
                else:
                    if not (Decimal('-34') <= lat <= Decimal('6')):
                        row_has_required_value = False
                        invalid_values['vl_latitude_limite_sicor'] = invalid_values.get('vl_latitude_limite_sicor', 0) + 1
                    if not (Decimal('-74') <= lon <= Decimal('-30')):
                        row_has_required_value = False
                        invalid_values['vl_longitude_limite_sicor'] = invalid_values.get('vl_longitude_limite_sicor', 0) + 1
                    if altitude is not None and not (Decimal('-100') <= altitude <= Decimal('3000')):
                        row_has_required_value = False
                        invalid_values['cgl_vl_altitude_limite_sicor'] = invalid_values.get('cgl_vl_altitude_limite_sicor', 0) + 1
                    if point_index < 0 or point_index > 100:
                        row_has_required_value = False
                        invalid_values['nu_indice_ponto_limite_sicor'] = invalid_values.get('nu_indice_ponto_limite_sicor', 0) + 1

            geom_wkt = None
            if spec.data_kind == 'sicor_wkt':
                source_name = mapping.get(spec.geometry_wkt_field)
                geom_wkt, geometry_reason, repaired = _polygonal_geometry(row.get(source_name) if source_name else None)
                if repaired:
                    geometry_repaired += 1
                if geom_wkt is None:
                    geometry_pending += 1
                    if len(geometry_samples) < 50:
                        geometry_samples.append({
                            'linha': line_number,
                            'ref_bacen': row.get(mapping.get('ref_bacen')) if mapping.get('ref_bacen') else '',
                            'nu_ordem': row.get(mapping.get('nu_ordem')) if mapping.get('nu_ordem') else '',
                            'motivo': geometry_reason,
                        })

            if row_has_required_value and (spec.data_kind != 'sicor_wkt' or geom_wkt is not None):
                op_batch.append((line_number, year, *canonical, *([geom_wkt] if spec.data_kind == 'sicor_wkt' else [])))
                operational += 1

            if len(raw_batch) >= batch_size:
                flush_batches()

        flush_batches()
        _progress(progress_callback, 66, f'Leitura SICOR concluída — {total:,} registros'.replace(',', '.'))
    finally:
        handle.close()

    return {
        'registros_recebidos': total,
        'registros_operacionais': operational,
        'registros_pendentes': total - operational,
        'valores_invalidos_por_campo': invalid_values,
        'campos_obrigatorios_vazios_por_campo': required_empty,
        'geometrias_pendentes': geometry_pending,
        'geometrias_reparadas': geometry_repaired,
        'amostras_geometrias_pendentes': geometry_samples,
        'fingerprint_sha256': fingerprint.hexdigest(),
    }


def _prepare_ready_operational(staging, op_staging, spec, progress_callback=None):
    """Converte tipos/geometria antes do checkpoint atômico de publicação.

    A etapa pesada fica no staging e mantém a versão ativa disponível. O bloco
    atômico final passa a fazer apenas a troca lógica dos dados já preparados.
    """
    ready_table = 'sicor_operational_ready'
    with connection.cursor() as cursor:
        defs = [sql.SQL('_numero_linha bigint NOT NULL'), sql.SQL('_ano_arquivo integer')]
        for field in spec.fields:
            defs.append(sql.SQL('{} {}').format(sql.Identifier(field.canonical), sql.SQL(_stable_type(field.sql_type))))
        if spec.data_kind == 'sicor_wkt':
            defs.append(sql.SQL(f'geom geometry(MultiPolygon, {int(spec.geometry_srid or 4674)})'))
        cursor.execute(sql.SQL('DROP TABLE IF EXISTS {}').format(_relation(staging, ready_table)))
        cursor.execute(sql.SQL('CREATE UNLOGGED TABLE {} ({})').format(
            _relation(staging, ready_table), sql.SQL(', ').join(defs)
        ))
        cursor.execute(
            sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} ({})').format(
                sql.Identifier('sicor_ready_line_idx'),
                _relation(staging, op_staging),
                sql.Identifier('_numero_linha'),
            )
        )
        cursor.execute(sql.SQL('ANALYZE {}').format(_relation(staging, op_staging)))
        cursor.execute(sql.SQL('SELECT COALESCE(MAX(_numero_linha), 0) FROM {}').format(_relation(staging, op_staging)))
        max_line = int(cursor.fetchone()[0] or 0)

    if max_line <= 0:
        return ready_table

    target_columns = ['_numero_linha', '_ano_arquivo', *[f.canonical for f in spec.fields]]
    select_exprs = [sql.Identifier('_numero_linha'), sql.Identifier('_ano_arquivo')]
    select_exprs.extend(_typed_select_expression(field) for field in spec.fields)
    if spec.data_kind == 'sicor_wkt':
        target_columns.append('geom')
        select_exprs.append(
            sql.SQL('ST_SetSRID(ST_GeomFromText(_geom_wkt), {})').format(sql.Literal(int(spec.geometry_srid or 4674)))
        )

    chunk = 50_000
    for start_line in range(0, max_line + 1, chunk):
        end_line = start_line + chunk - 1
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL('INSERT INTO {} ({}) SELECT {} FROM {} WHERE _numero_linha BETWEEN %s AND %s').format(
                    _relation(staging, ready_table),
                    sql.SQL(', ').join(sql.Identifier(c) for c in target_columns),
                    sql.SQL(', ').join(select_exprs),
                    _relation(staging, op_staging),
                ),
                [start_line, end_line],
            )
        ratio = min(1.0, end_line / max_line)
        _progress(progress_callback, 70 + int(round(ratio * 8)), 'Preparando publicação SICOR no staging')

    with connection.cursor() as cursor:
        cursor.execute(sql.SQL('ANALYZE {}').format(_relation(staging, ready_table)))
    _progress(progress_callback, 78, 'Staging SICOR pronto para publicação atômica')
    return ready_table


def _ensure_schema(schema):
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))


def _ensure_raw_table(schema, spec, raw_columns):
    with connection.cursor() as cursor:
        if not table_exists(schema, spec.raw_table):
            defs = [
                sql.SQL('_id bigserial PRIMARY KEY'),
                sql.SQL('_numero_linha bigint NOT NULL'),
                sql.SQL('_ano_arquivo integer'),
                sql.SQL('_arquivo_origem text NOT NULL'),
            ] + [sql.SQL('{} text').format(sql.Identifier(column)) for column in raw_columns]
            cursor.execute(sql.SQL('CREATE TABLE {} ({})').format(_relation(schema, spec.raw_table), sql.SQL(', ').join(defs)))
        else:
            for column in raw_columns:
                cursor.execute(
                    sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} text').format(
                        _relation(schema, spec.raw_table), sql.Identifier(column)
                    )
                )

        idx_name = f'{spec.raw_table[:48]}_ano_idx'
        if spec.year_partitioned:
            cursor.execute(
                sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} ({})').format(
                    sql.Identifier(idx_name), _relation(schema, spec.raw_table), sql.Identifier('_ano_arquivo')
                )
            )
        elif spec.data_kind == 'sicor_gleba_points':
            # O snapshot nacional de pontos não usa _ano_arquivo. Manter um índice
            # de milhões de entradas nulas só torna a carga mais lenta.
            cursor.execute(
                sql.SQL('DROP INDEX IF EXISTS {}.{}').format(sql.Identifier(schema), sql.Identifier(idx_name))
            )


def _stable_type(sql_type: str):
    return {'integer': 'bigint', 'numeric': 'numeric', 'date': 'date'}.get(str(sql_type).lower(), 'text')


def _is_gleba_points(spec):
    return spec.data_kind == 'sicor_gleba_points'


def _ensure_gleba_points_operational_table(schema, spec):
    srid = int(spec.geometry_srid or 4674)
    with connection.cursor() as cursor:
        if not table_exists(schema, spec.stable_table):
            cursor.execute(
                sql.SQL(
                    'CREATE TABLE {} ('
                    '_id bigserial PRIMARY KEY, '
                    'ref_bacen text NOT NULL, '
                    'nu_ordem bigint NOT NULL, '
                    'nu_identificador bigint NOT NULL, '
                    'nu_indice_gleba bigint NOT NULL, '
                    'qtd_pontos integer NOT NULL, '
                    'altitude_min numeric, '
                    'altitude_max numeric, '
                    'area_ha_calculada numeric, '
                    f'geom geometry(MultiPolygon, {srid}) NOT NULL'
                    ')'
                ).format(_relation(schema, spec.stable_table))
            )
        else:
            cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS qtd_pontos integer').format(_relation(schema, spec.stable_table)))
            cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS altitude_min numeric').format(_relation(schema, spec.stable_table)))
            cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS altitude_max numeric').format(_relation(schema, spec.stable_table)))
            cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS area_ha_calculada numeric').format(_relation(schema, spec.stable_table)))
            cursor.execute(
                sql.SQL(f'ALTER TABLE {{}} ADD COLUMN IF NOT EXISTS geom geometry(MultiPolygon, {srid})').format(
                    _relation(schema, spec.stable_table)
                )
            )
        idx_prefix = spec.stable_table[:40]
        cursor.execute(sql.SQL('CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} ({}, {}, {}, {})').format(
            sql.Identifier(f'{idx_prefix}_key_uidx'), _relation(schema, spec.stable_table),
            sql.Identifier('ref_bacen'), sql.Identifier('nu_ordem'), sql.Identifier('nu_identificador'), sql.Identifier('nu_indice_gleba')
        ))
        cursor.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} ({}, {})').format(
            sql.Identifier(f'{idx_prefix}_ref_ord_idx'), _relation(schema, spec.stable_table),
            sql.Identifier('ref_bacen'), sql.Identifier('nu_ordem')
        ))
        cursor.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} USING GIST ({})').format(
            sql.Identifier(f'{idx_prefix}_geom_gix'), _relation(schema, spec.stable_table), sql.Identifier('geom')
        ))


def _ensure_operational_table(schema, spec):
    if _is_gleba_points(spec):
        _ensure_gleba_points_operational_table(schema, spec)
        return
    with connection.cursor() as cursor:
        if not table_exists(schema, spec.stable_table):
            defs = [sql.SQL('_id bigserial PRIMARY KEY'), sql.SQL('_numero_linha bigint NOT NULL'), sql.SQL('_ano_arquivo integer')]
            for field in spec.fields:
                defs.append(sql.SQL('{} {}').format(sql.Identifier(field.canonical), sql.SQL(_stable_type(field.sql_type))))
            if spec.data_kind == 'sicor_wkt':
                defs.append(sql.SQL(f'geom geometry(MultiPolygon, {int(spec.geometry_srid or 4674)})'))
            cursor.execute(sql.SQL('CREATE TABLE {} ({})').format(_relation(schema, spec.stable_table), sql.SQL(', ').join(defs)))
        else:
            # Evolução aditiva: campos oficiais novos mapeados em versões futuras
            # podem ser adicionados sem recriar a tabela. Nunca removemos colunas.
            for field in spec.fields:
                cursor.execute(
                    sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {}').format(
                        _relation(schema, spec.stable_table), sql.Identifier(field.canonical), sql.SQL(_stable_type(field.sql_type))
                    )
                )
            cursor.execute(
                sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} bigint').format(
                    _relation(schema, spec.stable_table), sql.Identifier('_numero_linha')
                )
            )
            cursor.execute(
                sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} integer').format(
                    _relation(schema, spec.stable_table), sql.Identifier('_ano_arquivo')
                )
            )
            if spec.data_kind == 'sicor_wkt':
                cursor.execute(
                    sql.SQL(f'ALTER TABLE {{}} ADD COLUMN IF NOT EXISTS geom geometry(MultiPolygon, {int(spec.geometry_srid or 4674)})').format(
                        _relation(schema, spec.stable_table)
                    )
                )

        idx_prefix = spec.stable_table[:45]
        canonical_names = {f.canonical for f in spec.fields}
        if 'ref_bacen' in canonical_names:
            cursor.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} ({})').format(
                sql.Identifier(f'{idx_prefix}_ref_idx'), _relation(schema, spec.stable_table), sql.Identifier('ref_bacen')
            ))
        if {'ref_bacen', 'nu_ordem'} <= canonical_names:
            cursor.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} ({}, {})').format(
                sql.Identifier(f'{idx_prefix}_ref_ord_idx'), _relation(schema, spec.stable_table),
                sql.Identifier('ref_bacen'), sql.Identifier('nu_ordem')
            ))
        if 'cd_car' in canonical_names:
            cursor.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} ({})').format(
                sql.Identifier(f'{idx_prefix}_car_idx'), _relation(schema, spec.stable_table), sql.Identifier('cd_car')
            ))
        if spec.year_partitioned:
            cursor.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} ({})').format(
                sql.Identifier(f'{idx_prefix}_ano_idx'), _relation(schema, spec.stable_table), sql.Identifier('_ano_arquivo')
            ))
        if spec.data_kind == 'sicor_wkt':
            cursor.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} USING GIST ({})').format(
                sql.Identifier(f'{idx_prefix}_geom_gix'), _relation(schema, spec.stable_table), sql.Identifier('geom')
            ))


def _typed_select_expression(field):
    source = sql.Identifier(field.canonical)
    sql_type = str(field.sql_type or 'text').lower()
    if sql_type == 'integer':
        return sql.SQL('NULLIF({}, \'\')::bigint').format(source)
    if sql_type == 'numeric':
        return sql.SQL('NULLIF({}, \'\')::numeric').format(source)
    if sql_type == 'date':
        return sql.SQL('NULLIF({}, \'\')::date').format(source)
    return source


def _prepare_gleba_points_aggregate(staging, op_staging, spec, progress_callback=None):
    """Reconstrói as glebas no staging antes de tocar a base ativa.

    O arquivo nacional possui milhões de pontos. Primeiro convertemos o staging
    textual para uma tabela tipada e menor; depois indexamos a chave da gleba e
    só então agrupamos os vértices. Isso evita repetir CASTs durante o GROUP BY.
    """
    srid = int(spec.geometry_srid or 4674)
    points_table = 'sicor_gleba_points_ready'
    aggregate_table = 'sicor_glebas_aggregate'

    _progress(progress_callback, 70, 'Convertendo pontos SICOR para staging tipado')
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL('DROP TABLE IF EXISTS {}').format(_relation(staging, points_table)))
        cursor.execute(sql.SQL('DROP TABLE IF EXISTS {}').format(_relation(staging, aggregate_table)))
        cursor.execute(
            sql.SQL(r'''
                CREATE UNLOGGED TABLE {} AS
                SELECT
                    ref_bacen,
                    NULLIF(nu_ordem, '')::bigint AS nu_ordem,
                    NULLIF(nu_identificador, '')::bigint AS nu_identificador,
                    NULLIF(nu_indice_gleba, '')::bigint AS nu_indice_gleba,
                    NULLIF(nu_indice_ponto, '')::integer AS nu_indice_ponto,
                    NULLIF(vl_latitude, '')::double precision AS lat,
                    NULLIF(vl_longitude, '')::double precision AS lon,
                    NULLIF(cgl_vl_altitude, '')::numeric AS altitude
                FROM {}
                WHERE NULLIF(ref_bacen, '') IS NOT NULL
                  AND NULLIF(nu_ordem, '') IS NOT NULL
                  AND NULLIF(nu_identificador, '') IS NOT NULL
                  AND NULLIF(nu_indice_gleba, '') IS NOT NULL
                  AND NULLIF(nu_indice_ponto, '') IS NOT NULL
                  AND NULLIF(vl_latitude, '') IS NOT NULL
                  AND NULLIF(vl_longitude, '') IS NOT NULL
            ''').format(_relation(staging, points_table), _relation(staging, op_staging))
        )

    _progress(progress_callback, 72, 'Indexando sequência dos vértices SICOR')
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL('CREATE INDEX {} ON {} ({}, {}, {}, {}, {})').format(
                sql.Identifier('sicor_gleba_points_order_idx'),
                _relation(staging, points_table),
                sql.Identifier('ref_bacen'), sql.Identifier('nu_ordem'),
                sql.Identifier('nu_identificador'), sql.Identifier('nu_indice_gleba'),
                sql.Identifier('nu_indice_ponto'),
            )
        )
        cursor.execute(sql.SQL('ANALYZE {}').format(_relation(staging, points_table)))

    _progress(progress_callback, 74, 'Reconstruindo polígonos das glebas SICOR')
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(r'''
                CREATE UNLOGGED TABLE {} AS
                WITH grupos AS (
                    SELECT
                        ref_bacen, nu_ordem, nu_identificador, nu_indice_gleba,
                        COUNT(*)::integer AS qtd_pontos,
                        COUNT(DISTINCT nu_indice_ponto)::integer AS qtd_indices,
                        MIN(nu_indice_ponto)::integer AS indice_min,
                        MAX(nu_indice_ponto)::integer AS indice_max,
                        MIN(altitude) AS altitude_min,
                        MAX(altitude) AS altitude_max,
                        ST_MakeLine(
                            ST_SetSRID(ST_MakePoint(lon, lat), {})
                            ORDER BY nu_indice_ponto
                        ) AS linha
                    FROM {}
                    GROUP BY ref_bacen, nu_ordem, nu_identificador, nu_indice_gleba
                )
                SELECT *,
                    CASE
                        WHEN qtd_pontos >= 4
                         AND qtd_pontos <= 100
                         AND qtd_indices = qtd_pontos
                         AND (indice_max - indice_min + 1) = qtd_pontos
                         AND ST_IsClosed(linha)
                        THEN ST_MakePolygon(linha)
                        ELSE NULL
                    END AS geom_original
                FROM grupos
            ''').format(
                _relation(staging, aggregate_table),
                sql.Literal(srid),
                _relation(staging, points_table),
            )
        )

    _progress(progress_callback, 76, 'Validando e reparando geometrias das glebas SICOR')
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL('ALTER TABLE {} ADD COLUMN geom geometry(MultiPolygon, {})').format(
                _relation(staging, aggregate_table), sql.Literal(srid)
            )
        )
        cursor.execute(
            sql.SQL(
                'UPDATE {} '
                'SET geom = ST_Multi(ST_CollectionExtract(ST_MakeValid(geom_original), 3)) '
                'WHERE geom_original IS NOT NULL'
            ).format(_relation(staging, aggregate_table))
        )
        cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN area_ha_calculada numeric').format(
            _relation(staging, aggregate_table)
        ))
        cursor.execute(
            sql.SQL(
                'UPDATE {} SET area_ha_calculada = '
                'ROUND((ST_Area(ST_Transform(geom, 4326)::geography) / 10000.0)::numeric, 6) '
                'WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom) AND ST_IsValid(geom)'
            ).format(_relation(staging, aggregate_table))
        )
        cursor.execute(sql.SQL('ANALYZE {}').format(_relation(staging, aggregate_table)))

        cursor.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(_relation(staging, aggregate_table)))
        total_glebas = int(cursor.fetchone()[0] or 0)
        cursor.execute(sql.SQL('SELECT COUNT(*) FROM {} WHERE geom_original IS NULL').format(_relation(staging, aggregate_table)))
        estrutura_pendente = int(cursor.fetchone()[0] or 0)
        cursor.execute(sql.SQL('SELECT COUNT(*) FROM {} WHERE geom_original IS NOT NULL AND NOT ST_IsValid(geom_original)').format(_relation(staging, aggregate_table)))
        invalidas_originais = int(cursor.fetchone()[0] or 0)
        cursor.execute(
            sql.SQL(
                'SELECT COUNT(*) FROM {} '
                'WHERE geom_original IS NOT NULL AND NOT ST_IsValid(geom_original) '
                'AND geom IS NOT NULL AND NOT ST_IsEmpty(geom) AND ST_IsValid(geom)'
            ).format(_relation(staging, aggregate_table))
        )
        reparadas = int(cursor.fetchone()[0] or 0)
        cursor.execute(sql.SQL('SELECT COUNT(*) FROM {} WHERE geom IS NULL OR ST_IsEmpty(geom) OR NOT ST_IsValid(geom)').format(_relation(staging, aggregate_table)))
        geometrias_pendentes = int(cursor.fetchone()[0] or 0)
        cursor.execute(
            sql.SQL(r'''
                SELECT ref_bacen, nu_ordem, nu_identificador, nu_indice_gleba, qtd_pontos
                FROM {}
                WHERE geom IS NULL OR ST_IsEmpty(geom) OR NOT ST_IsValid(geom)
                LIMIT 30
            ''').format(_relation(staging, aggregate_table))
        )
        samples = [
            {
                'ref_bacen': row[0], 'nu_ordem': row[1],
                'nu_identificador': row[2], 'nu_indice_gleba': row[3],
                'qtd_pontos': row[4],
            }
            for row in cursor.fetchall()
        ]

    _progress(progress_callback, 78, f'{total_glebas:,} glebas preparadas no staging'.replace(',', '.'))
    return aggregate_table, {
        'glebas_identificadas': total_glebas,
        'glebas_estrutura_pendente': estrutura_pendente,
        'geometrias_invalidas_originais': invalidas_originais,
        'geometrias_reparadas': reparadas,
        'geometrias_pendentes': geometrias_pendentes,
        'amostras_pendencias': samples,
    }


def _promote_gleba_points(staging, raw_staging, op_staging, spec, raw_columns, progress_callback=None):
    schema = FONTE_SCHEMAS[spec.fonte]
    srid = int(spec.geometry_srid or 4674)
    _ensure_schema(schema)

    aggregate_table, aggregate_stats = _prepare_gleba_points_aggregate(
        staging, op_staging, spec, progress_callback=progress_callback,
    )
    _progress(progress_callback, 82, 'Publicando snapshot SICOR de forma atômica')

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', [f'confronta:{schema}:{spec.stable_table}'])
            _ensure_raw_table(schema, spec, raw_columns)
            _ensure_operational_table(schema, spec)
            cursor.execute(sql.SQL('TRUNCATE TABLE {}').format(_relation(schema, spec.raw_table)))
            cursor.execute(sql.SQL('TRUNCATE TABLE {} RESTART IDENTITY').format(_relation(schema, spec.stable_table)))

            raw_target_columns = ['_numero_linha', '_ano_arquivo', '_arquivo_origem', *raw_columns]
            cursor.execute(
                sql.SQL('INSERT INTO {} ({}) SELECT {} FROM {}').format(
                    _relation(schema, spec.raw_table),
                    sql.SQL(', ').join(sql.Identifier(c) for c in raw_target_columns),
                    sql.SQL(', ').join(sql.Identifier(c) for c in raw_target_columns),
                    _relation(staging, raw_staging),
                )
            )

            cursor.execute(
                sql.SQL(r'''
                    INSERT INTO {} (
                        ref_bacen, nu_ordem, nu_identificador, nu_indice_gleba,
                        qtd_pontos, altitude_min, altitude_max, area_ha_calculada, geom
                    )
                    SELECT
                        ref_bacen, nu_ordem, nu_identificador, nu_indice_gleba,
                        qtd_pontos, altitude_min, altitude_max,
                        area_ha_calculada,
                        geom
                    FROM {}
                    WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom) AND ST_IsValid(geom)
                ''').format(_relation(schema, spec.stable_table), _relation(staging, aggregate_table))
            )
            cursor.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(_relation(schema, spec.raw_table)))
            raw_total = int(cursor.fetchone()[0] or 0)
            cursor.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(_relation(schema, spec.stable_table)))
            operational_total = int(cursor.fetchone()[0] or 0)

    _progress(progress_callback, 96, 'Snapshot SICOR publicado; finalizando auditoria')
    return {
        'schema': schema,
        'tabela_raw': spec.raw_table,
        'tabela_operacional': spec.stable_table,
        'registros_raw_total_tabela': raw_total,
        'registros_operacionais_total_tabela': operational_total,
        'pontos_recebidos_raw': raw_total,
        'glebas_publicadas': operational_total,
        **aggregate_stats,
        'srid': srid,
        'modo': 'SUBSTITUI_TABELA',
    }


def _promote(staging, raw_staging, op_staging, spec, raw_columns, year, progress_callback=None):
    if _is_gleba_points(spec):
        return _promote_gleba_points(
            staging, raw_staging, op_staging, spec, raw_columns,
            progress_callback=progress_callback,
        )

    schema = FONTE_SCHEMAS[spec.fonte]
    _ensure_schema(schema)
    ready_table = _prepare_ready_operational(
        staging, op_staging, spec, progress_callback=progress_callback,
    )
    _progress(progress_callback, 82, 'Aplicando publicação atômica SICOR')

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', [f'confronta:{schema}:{spec.stable_table}'])
            _ensure_raw_table(schema, spec, raw_columns)
            _ensure_operational_table(schema, spec)

            if spec.year_partitioned:
                cursor.execute(sql.SQL('DELETE FROM {} WHERE _ano_arquivo = %s').format(_relation(schema, spec.raw_table)), [year])
                cursor.execute(sql.SQL('DELETE FROM {} WHERE _ano_arquivo = %s').format(_relation(schema, spec.stable_table)), [year])
            else:
                cursor.execute(sql.SQL('TRUNCATE TABLE {}').format(_relation(schema, spec.raw_table)))
                cursor.execute(sql.SQL('TRUNCATE TABLE {} RESTART IDENTITY').format(_relation(schema, spec.stable_table)))

            raw_target_columns = ['_numero_linha', '_ano_arquivo', '_arquivo_origem', *raw_columns]
            cursor.execute(
                sql.SQL('INSERT INTO {} ({}) SELECT {} FROM {}').format(
                    _relation(schema, spec.raw_table),
                    sql.SQL(', ').join(sql.Identifier(c) for c in raw_target_columns),
                    sql.SQL(', ').join(sql.Identifier(c) for c in raw_target_columns),
                    _relation(staging, raw_staging),
                )
            )

            op_target_columns = ['_numero_linha', '_ano_arquivo', *[f.canonical for f in spec.fields]]
            if spec.data_kind == 'sicor_wkt':
                op_target_columns.append('geom')
            cursor.execute(
                sql.SQL('INSERT INTO {} ({}) SELECT {} FROM {}').format(
                    _relation(schema, spec.stable_table),
                    sql.SQL(', ').join(sql.Identifier(c) for c in op_target_columns),
                    sql.SQL(', ').join(sql.Identifier(c) for c in op_target_columns),
                    _relation(staging, ready_table),
                )
            )
            cursor.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(_relation(schema, spec.raw_table)))
            raw_total = int(cursor.fetchone()[0] or 0)
            cursor.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(_relation(schema, spec.stable_table)))
            operational_total = int(cursor.fetchone()[0] or 0)

    _progress(progress_callback, 96, 'SICOR publicado; finalizando auditoria')
    return {
        'schema': schema,
        'tabela_raw': spec.raw_table,
        'tabela_operacional': spec.stable_table,
        'registros_raw_total_tabela': raw_total,
        'registros_operacionais_total_tabela': operational_total,
        'particao_ano_substituida': year if spec.year_partitioned else None,
        'modo': 'SUBSTITUI_ANO' if spec.year_partitioned else 'SUBSTITUI_TABELA',
    }


def _upsert_layer(spec, imp, signature):
    schema = FONTE_SCHEMAS[spec.fonte]
    now = timezone.now()
    obj, created = CamadaImportada.objects.get_or_create(
        fonte=spec.fonte,
        dataset_slug=spec.slug,
        schema_banco=schema,
        nome_tabela=spec.stable_table,
        defaults={
            'nome_original': imp.nome_arquivo_original,
            'tabela_raw': spec.raw_table,
            'tipo_geometria': 'MULTIPOLYGON' if spec.data_kind in {'sicor_wkt', 'sicor_gleba_points'} else '',
            'srid': int(spec.geometry_srid or 4674) if spec.data_kind in {'sicor_wkt', 'sicor_gleba_points'} else None,
            'assinatura_estrutura': signature,
            'primeira_importacao': now,
            'ultima_importacao': now,
            'status': CamadaImportada.Status.ATIVA,
            'ultima_importacao_ref': imp,
        },
    )
    if not created:
        obj.nome_original = imp.nome_arquivo_original
        obj.tabela_raw = spec.raw_table
        obj.tipo_geometria = 'MULTIPOLYGON' if spec.data_kind in {'sicor_wkt', 'sicor_gleba_points'} else ''
        obj.srid = int(spec.geometry_srid or 4674) if spec.data_kind in {'sicor_wkt', 'sicor_gleba_points'} else None
        obj.assinatura_estrutura = signature
        obj.ultima_importacao = now
        obj.status = CamadaImportada.Status.ATIVA
        obj.data_sem_uso = None
        obj.ultima_importacao_ref = imp
        obj.save(update_fields=[
            'nome_original', 'tabela_raw', 'tipo_geometria', 'srid', 'assinatura_estrutura',
            'ultima_importacao', 'status', 'data_sem_uso', 'ultima_importacao_ref',
        ])
    return obj


def process_sicor_import(uploaded_file, spec, usuario, context=None, progress_callback=None):
    context = dict(context or {})
    _progress(progress_callback, 2, 'Registrando importação SICOR')
    imp = Importacao.objects.create(
        fonte=spec.fonte,
        dataset_slug=spec.slug,
        dataset_label=spec.label,
        nome_arquivo_original=Path(uploaded_file.name).name,
        hash_sha256='0' * 64,
        tamanho_bytes=0,
        administrador=usuario,
        status=Importacao.Status.RECEBIDO,
        contexto=context,
    )
    quarantine = None
    workdir = None
    staging = None
    try:
        if settings.MAX_UPLOAD_SIZE_BYTES and uploaded_file.size > settings.MAX_UPLOAD_SIZE_BYTES:
            raise SecurityValidationError('O arquivo excede o limite configurado para upload.')

        suffix = Path(uploaded_file.name).suffix.lower()
        if suffix not in {'.gz', '.csv'}:
            raise SecurityValidationError('O perfil SICOR selecionado aceita somente arquivos .gz ou .csv.')

        _progress(progress_callback, 8, 'Recebendo arquivo SICOR')
        quarantine, digest, size = _save_upload(uploaded_file, imp.pk)
        imp.hash_sha256 = digest
        imp.tamanho_bytes = size
        imp.quarantine_path = str(quarantine.relative_to(settings.BASE_DIR))
        imp.status = Importacao.Status.VALIDANDO
        imp.save(update_fields=['hash_sha256', 'tamanho_bytes', 'quarantine_path', 'status'])

        year = _extract_year(imp.nome_arquivo_original) if spec.year_partitioned else None
        if spec.year_partitioned and year is None:
            raise DatasetIdentityError(
                'Este perfil SICOR é anual, mas o ano não pôde ser identificado no nome do arquivo.',
                {'status': 'NAO_CONFIRMADO', 'arquivo': imp.nome_arquivo_original, 'dataset': spec.slug},
            )

        existing_layer = CamadaImportada.objects.filter(
            fonte=spec.fonte, dataset_slug=spec.slug, schema_banco=FONTE_SCHEMAS[spec.fonte],
            nome_tabela=spec.stable_table, status=CamadaImportada.Status.ATIVA,
        ).first()
        duplicate = None
        if existing_layer:
            for candidate in (
                Importacao.objects.filter(dataset_slug=spec.slug, hash_sha256=digest, status=Importacao.Status.CONCLUIDO)
                .exclude(pk=imp.pk).order_by('-data_inicio')[:20]
            ):
                previous_year = ((candidate.resultado or {}).get('sicor_csv') or {}).get('ano_arquivo')
                if spec.year_partitioned and previous_year != year:
                    continue
                duplicate = candidate
                break
        if duplicate:
            imp.status = Importacao.Status.IGNORADO_DUPLICADO
            imp.identidade_status = 'DUPLICADO'
            imp.data_finalizacao = timezone.now()
            imp.resultado = {
                'duplicado': True,
                'importacao_anterior_id': duplicate.pk,
                'motivo': 'SHA-256 idêntico a uma importação concluída deste perfil SICOR.',
                'sicor_csv': {'ano_arquivo': year, 'formato': suffix.lstrip('.')},
            }
            imp.save(update_fields=['status', 'identidade_status', 'data_finalizacao', 'resultado'])
            registrar_auditoria(usuario, 'IMPORTACAO_SICOR_DUPLICADA', 'Importacao', imp.pk, imp.resultado)
            return imp

        _progress(progress_callback, 18, 'Validando GZIP/CSV e antimalware')
        antivirus = run_antivirus(quarantine)
        csv_path, workdir, security = _prepare_csv(quarantine, imp.pk)

        _progress(progress_callback, 30, 'Identificando estrutura oficial SICOR')
        csv_info = _detect_csv_format(csv_path, spec)
        identity = _validate_identity(spec, imp.nome_arquivo_original, csv_info)
        identity['ano_arquivo'] = year
        previous_headers, previous_import_id = _previous_headers(spec, imp.pk)
        current_header_set = set(csv_info['headers'])
        previous_header_set = set(previous_headers)
        schema_header_changes = {
            'referencia_importacao_id': previous_import_id,
            'campos_adicionados': sorted(current_header_set - previous_header_set) if previous_headers else [],
            'campos_ausentes_na_nova_versao': sorted(previous_header_set - current_header_set) if previous_headers else [],
        }
        identity['mudancas_de_cabecalho'] = schema_header_changes
        if spec.data_kind == 'sicor_wkt':
            identity['geometria'] = 'POLYGON/MULTIPOLYGON derivada de GT_GEOMETRIA'
            identity['srid'] = int(spec.geometry_srid or 4674)
        elif spec.data_kind == 'sicor_gleba_points':
            identity['geometria'] = 'MULTIPOLYGON reconstruída da sequência NU_INDICE_PONTO / VL_LONGITUDE / VL_LATITUDE'
            identity['srid'] = int(spec.geometry_srid or 4674)
            identity['chave_gleba'] = ['REF_BACEN', 'NU_ORDEM', 'NU_IDENTIFICADOR', 'NU_INDICE_GLEBA']
            identity['ordem_vertices'] = 'NU_INDICE_PONTO'
        imp.identidade_status = 'CONFIRMADO'
        imp.identidade_relatorio = identity
        imp.status = Importacao.Status.VALIDANDO_IDENTIDADE
        imp.save(update_fields=['identidade_status', 'identidade_relatorio', 'status'])

        _progress(progress_callback, 42, 'Preparando staging SICOR')
        staging = create_staging_schema(imp.pk)
        raw_staging, op_staging = _create_staging_tables(staging, csv_info['headers'], spec)
        imp.status = Importacao.Status.IMPORTANDO
        imp.save(update_fields=['status'])

        _progress(progress_callback, 52, 'Lendo CSV em streaming')
        stats = _copy_rows(
            csv_path, csv_info, identity, spec, staging, raw_staging, op_staging, year, imp.nome_arquivo_original,
            progress_callback=progress_callback,
        )
        if stats['registros_recebidos'] <= 0:
            raise DatasetIdentityError('O arquivo SICOR não possui registros de dados.', {'status': 'NAO_CONFIRMADO'})
        if stats['registros_operacionais'] <= 0:
            raise DatasetIdentityError(
                'Nenhum registro pôde ser validado para a tabela operacional; a RAW temporária não foi promovida.',
                {
                    'status': 'NAO_CONFIRMADO',
                    'registros_recebidos': stats['registros_recebidos'],
                    'registros_pendentes': stats['registros_pendentes'],
                },
            )

        fingerprint = stats['fingerprint_sha256']
        _progress(progress_callback, 68, 'Comparando conteúdo com a versão atual')
        previous_same = _fingerprint_previous(spec, imp.pk, fingerprint, year)
        if previous_same:
            imp.status = Importacao.Status.SEM_ALTERACAO
            imp.data_finalizacao = timezone.now()
            imp.resultado = {
                'sem_alteracao': True,
                'motivo': 'O conteúdo CSV tratado é idêntico à versão já ativa. Nenhuma escrita foi realizada.',
                'importacao_anterior_id': previous_same.pk,
                'fingerprint_conteudo': {'sha256': fingerprint},
                'sicor_csv': {
                    'ano_arquivo': year,
                    'encoding': csv_info['encoding'],
                    'delimitador': repr(csv_info['delimiter']),
                    **stats,
                },
                'seguranca_arquivo': security,
                'antimalware': antivirus,
            }
            imp.save(update_fields=['status', 'data_finalizacao', 'resultado'])
            registrar_auditoria(usuario, 'IMPORTACAO_SICOR_SEM_ALTERACAO', 'Importacao', imp.pk, imp.resultado)
            return imp

        _progress(progress_callback, 69, 'Preparando publicação SICOR')
        promotion = _promote(
            staging, raw_staging, op_staging, spec, csv_info['headers'], year,
            progress_callback=progress_callback,
        )
        structure_signature = hashlib.sha256(
            ('|'.join(csv_info['headers']) + '|' + '|'.join(f'{f.canonical}:{f.sql_type}' for f in spec.fields)).encode('utf-8')
        ).hexdigest()
        _upsert_layer(spec, imp, structure_signature)

        known_actual = {value for value in identity['mapeamento'].values() if value}
        schema_changes = {
            'campos_recebidos': csv_info['headers'],
            'campos_extras_raw': [h for h in csv_info['headers'] if h not in known_actual],
            'campos_operacionais_mapeados': [name for name, actual in identity['mapeamento'].items() if actual],
            'campos_adicionados_desde_ultima_versao': schema_header_changes.get('campos_adicionados', []),
            'campos_ausentes_desde_ultima_versao': schema_header_changes.get('campos_ausentes_na_nova_versao', []),
            'referencia_importacao_anterior_id': schema_header_changes.get('referencia_importacao_id'),
            'politica': 'RAW flexível e aditiva; operacional estável com campos mapeados.',
        }
        pending = int(stats['registros_pendentes'] or 0) + int(promotion.get('geometrias_pendentes') or 0)
        imp.status = Importacao.Status.CONCLUIDO
        imp.data_finalizacao = timezone.now()
        imp.resultado = {
            'sicor_csv': {
                'aplicado': True,
                'ano_arquivo': year,
                'encoding': csv_info['encoding'],
                'delimitador': repr(csv_info['delimiter']),
                'campos_recebidos': csv_info['headers'],
                **stats,
            },
            'fingerprint_conteudo': {'sha256': fingerprint},
            'promocao': promotion,
            'alteracoes_estrutura': schema_changes,
            'seguranca_arquivo': security,
            'antimalware': antivirus,
            'pendencias': {
                'quantidade': pending,
                'geometrias': int(stats['geometrias_pendentes'] or 0) + int(promotion.get('geometrias_pendentes') or 0),
                'valores_invalidos_por_campo': stats['valores_invalidos_por_campo'],
                'amostras_geometrias': stats['amostras_geometrias_pendentes'] or promotion.get('amostras_pendencias', []),
            },
        }
        imp.motivo_rejeicao = ''
        imp.save(update_fields=['status', 'data_finalizacao', 'resultado', 'motivo_rejeicao'])
        registrar_auditoria(
            usuario, 'IMPORTACAO_SICOR_CONCLUIDA', 'Importacao', imp.pk,
            {
                'dataset': spec.slug,
                'ano': year,
                'recebidos': stats['registros_recebidos'],
                'operacionais': stats['registros_operacionais'],
                'pendencias': pending,
                'fingerprint': fingerprint,
            },
        )
        _progress(progress_callback, 100, 'Importação SICOR concluída')
        return imp

    except SecurityValidationError as exc:
        imp.status = Importacao.Status.REJEITADO_SEGURANCA
        imp.data_finalizacao = timezone.now()
        imp.motivo_rejeicao = str(exc)
        imp.save(update_fields=['status', 'data_finalizacao', 'motivo_rejeicao'])
        registrar_auditoria(usuario, 'IMPORTACAO_SICOR_BLOQUEADA_SEGURANCA', 'Importacao', imp.pk, {'motivo': str(exc)})
        return imp
    except DatasetIdentityError as exc:
        imp.status = Importacao.Status.REJEITADO_IDENTIDADE
        imp.identidade_status = exc.report.get('status', 'NAO_CONFIRMADO')
        imp.identidade_relatorio = exc.report
        imp.data_finalizacao = timezone.now()
        imp.motivo_rejeicao = str(exc)
        imp.save(update_fields=['status', 'identidade_status', 'identidade_relatorio', 'data_finalizacao', 'motivo_rejeicao'])
        registrar_auditoria(usuario, 'IMPORTACAO_SICOR_BLOQUEADA_IDENTIDADE', 'Importacao', imp.pk, {'motivo': str(exc), 'relatorio': exc.report})
        return imp
    except BatchInterruptionRequested as exc:
        imp.status = Importacao.Status.INTERROMPIDO
        imp.data_finalizacao = timezone.now()
        imp.motivo_rejeicao = str(exc)
        imp.resultado = {
            'interrompida': True,
            'base_ativa_preservada': True,
            'contexto': context,
        }
        imp.save(update_fields=['status', 'data_finalizacao', 'motivo_rejeicao', 'resultado'])
        registrar_auditoria(
            usuario, 'IMPORTACAO_SICOR_INTERROMPIDA', 'Importacao', imp.pk,
            {'dataset': spec.slug, 'motivo': str(exc), 'base_ativa_preservada': True},
        )
        return imp

    except Exception as exc:
        logger.exception('Falha na importação SICOR %s', imp.pk)
        imp.status = Importacao.Status.FALHOU
        imp.data_finalizacao = timezone.now()
        imp.motivo_rejeicao = str(exc)
        imp.save(update_fields=['status', 'data_finalizacao', 'motivo_rejeicao'])
        registrar_auditoria(usuario, 'IMPORTACAO_SICOR_FALHOU', 'Importacao', imp.pk, {'motivo': str(exc), 'dataset': spec.slug})
        return imp
    finally:
        if staging:
            try:
                drop_schema(staging)
            except Exception:
                logger.exception('Não foi possível remover staging SICOR %s', staging)
        if workdir and Path(workdir).exists():
            shutil.rmtree(workdir, ignore_errors=True)
        if quarantine and Path(quarantine).exists():
            Path(quarantine).unlink(missing_ok=True)
