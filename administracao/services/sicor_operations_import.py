from __future__ import annotations

import csv
import gzip
import hashlib
import io
import logging
import time
import zlib
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from psycopg import sql

from administracao.constants import FONTE_SCHEMAS
from administracao.models import Importacao, SicorOperacao

from .auditoria import registrar_auditoria
from .exceptions import BatchInterruptionRequested, SecurityValidationError
from .postgis import create_staging_schema, drop_schema
from .sicor_import import _upsert_layer
from .sicor_operations import (
    INVALID_OPERATIONS_HEADER,
    SicorOperationsValidationError,
    _OPERATIONS_HEADER,
    operations_reference_year,
    validate_operations_header,
)
from .zip_security import run_antivirus


logger = logging.getLogger(__name__)

_RAW_STAGE_TABLE = 'sicor_operations_raw_incoming'
_TYPED_STAGE_TABLE = 'sicor_operations_typed_incoming'
_COPY_BATCH_SIZE = 5_000
_REJECTION_SAMPLE_LIMIT = 25


class SicorOperationsImportError(ValueError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report or {}


def _relation(schema, table):
    return sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))


def _canonical_header_name(header):
    # The validated official header is the sole mapping source. Its leading
    # hash is a file-format marker, not part of the database column name.
    return header[1:].lower() if header.startswith('#') else header.lower()


def _columns_and_types():
    columns = []
    for header in _OPERATIONS_HEADER:
        column = _canonical_header_name(header)
        try:
            field = SicorOperacao._meta.get_field(column)
        except Exception as exc:
            raise RuntimeError(f'Coluna SICOR sem campo correspondente no model: {column}') from exc
        columns.append((column, field))
    return columns


def _save_upload(uploaded_file, import_id):
    filename = Path(uploaded_file.name).name
    suffix = Path(filename).suffix.lower()
    target = Path(settings.QUARANTINE_DIR) / f'sicor_operacoes_{import_id}{suffix}'
    digest = hashlib.sha256()
    size = 0
    with target.open('wb') as destination:
        for chunk in uploaded_file.chunks():
            size += len(chunk)
            digest.update(chunk)
            destination.write(chunk)
        destination.flush()
    return target, digest.hexdigest(), size


def _create_staging_tables(staging, columns_and_fields):
    raw_relation = _relation(staging, _RAW_STAGE_TABLE)
    typed_relation = _relation(staging, _TYPED_STAGE_TABLE)
    raw_columns = [column for column, _field in columns_and_fields]
    typed_defs = [
        sql.SQL('_numero_linha bigint NOT NULL'),
        sql.SQL('_ano_arquivo integer NOT NULL'),
    ]
    for column, field in columns_and_fields:
        db_type = field.db_type(connection)
        if not db_type:
            raise RuntimeError(f'Tipo PostgreSQL não definido para a coluna {column}.')
        typed_defs.append(sql.SQL('{} {}').format(sql.Identifier(column), sql.SQL(db_type)))

    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL('CREATE UNLOGGED TABLE {} (_numero_linha bigint NOT NULL, _ano_arquivo integer NOT NULL, _arquivo_origem text NOT NULL, {})').format(
                raw_relation,
                sql.SQL(', ').join(sql.SQL('{} text').format(sql.Identifier(column)) for column in raw_columns),
            )
        )
        cursor.execute(
            sql.SQL('CREATE UNLOGGED TABLE {} ({})').format(
                typed_relation,
                sql.SQL(', ').join(typed_defs),
            )
        )


def _copy_batch(staging, table, columns, rows):
    if not rows:
        return
    statement = sql.SQL('COPY {} ({}) FROM STDIN').format(
        _relation(staging, table),
        sql.SQL(', ').join(sql.Identifier(column) for column in columns),
    )
    with connection.cursor() as cursor:
        with cursor.copy(statement) as copy:
            for row in rows:
                copy.write_row(row)


def _convert_value(value, field):
    text = str(value or '').strip()
    if not text:
        return None

    internal_type = field.get_internal_type()
    if internal_type == 'TextField':
        return text
    if internal_type == 'DateField':
        for date_format in ('%Y-%m-%d', '%d/%m/%Y', '%Y%m%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(text[:10], date_format).date()
            except ValueError:
                continue
        raise ValueError('data inválida')
    if internal_type == 'DecimalField':
        normalized = text.replace(' ', '')
        if ',' in normalized and '.' not in normalized:
            normalized = normalized.replace(',', '.')
        try:
            number = Decimal(normalized)
        except InvalidOperation as exc:
            raise ValueError('valor numérico inválido') from exc
        if not number.is_finite():
            raise ValueError('valor numérico não finito')
        return number
    if internal_type in {'BigIntegerField', 'IntegerField'}:
        try:
            number = Decimal(text.replace(' ', '').replace(',', '.'))
        except InvalidOperation as exc:
            raise ValueError('número inteiro inválido') from exc
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError('número inteiro inválido')
        return int(number)
    raise RuntimeError(f'Tipo de campo SICOR não tratado: {internal_type}')


def _typed_row(values, columns_and_fields, line_number):
    converted = []
    invalid = []
    for index, (column, field) in enumerate(columns_and_fields):
        try:
            converted.append(_convert_value(values[index], field))
        except ValueError:
            converted.append(None)
            invalid.append(column)

    # Identificadores ausentes não podem relacionar-se com as geometrias SICOR.
    for required in ('ref_bacen', 'nu_ordem'):
        if converted[[column for column, _field in columns_and_fields].index(required)] is None:
            if required not in invalid:
                invalid.append(required)
    return (line_number, converted, invalid)


def _open_text_stream(path, suffix):
    binary = gzip.open(path, 'rb') if suffix == '.gz' else path.open('rb')
    return binary, io.TextIOWrapper(binary, encoding='utf-8-sig', errors='strict', newline='')


def _bytes_consumed(binary, suffix):
    if suffix == '.gz':
        fileobj = getattr(binary, 'fileobj', None)
        return int(fileobj.tell()) if fileobj else 0
    return int(binary.tell())


def _stream_copy(path, filename, year, staging, columns_and_fields, file_size, progress_callback):
    suffix = Path(filename).suffix.lower()
    canonical_columns = [column for column, _field in columns_and_fields]
    raw_copy_columns = ['_numero_linha', '_ano_arquivo', '_arquivo_origem', *canonical_columns]
    typed_copy_columns = ['_numero_linha', '_ano_arquivo', *canonical_columns]

    raw_rows = []
    typed_rows = []
    records_read = 0
    records_imported = 0
    records_rejected = 0
    rejected_samples = []
    previous_physical_line = 1
    expanded_bytes = 0
    compression_ratio = None
    raw_binary, text_stream = _open_text_stream(path, suffix)
    csv.field_size_limit(max(csv.field_size_limit(), 32 * 1024 * 1024))
    try:
        reader = csv.reader(text_stream, delimiter=';')
        header = next(reader, None)
        if tuple(header or ()) != _OPERATIONS_HEADER:
            raise SicorOperationsValidationError(INVALID_OPERATIONS_HEADER)

        for values in reader:
            line_number = previous_physical_line + 1
            previous_physical_line = reader.line_num
            if not values or all(not str(value).strip() for value in values):
                continue
            records_read += 1
            if len(values) != len(_OPERATIONS_HEADER):
                raise SicorOperationsValidationError(
                    f'Linha {line_number}: esperado 47 campos, recebidos {len(values)}.'
                )

            raw_rows.append((line_number, year, filename, *values))
            _line_number, converted, invalid = _typed_row(values, columns_and_fields, line_number)
            if invalid:
                records_rejected += 1
                if len(rejected_samples) < _REJECTION_SAMPLE_LIMIT:
                    rejected_samples.append({'linha': line_number, 'campos': invalid})
            else:
                typed_rows.append((line_number, year, *converted))
                records_imported += 1

            if len(raw_rows) >= _COPY_BATCH_SIZE:
                _copy_batch(staging, _RAW_STAGE_TABLE, raw_copy_columns, raw_rows)
                _copy_batch(staging, _TYPED_STAGE_TABLE, typed_copy_columns, typed_rows)
                raw_rows.clear()
                typed_rows.clear()
                if suffix == '.gz':
                    expanded_bytes = int(raw_binary.tell())
                    expansion_limit = int(getattr(settings, 'MAX_ZIP_UNCOMPRESSED_BYTES', 0) or 0)
                    if expansion_limit and expanded_bytes > expansion_limit:
                        raise SecurityValidationError('O conteúdo descompactado excede o limite configurado.')
                consumed = min(file_size, _bytes_consumed(raw_binary, suffix))
                ratio = min(1.0, consumed / max(1, file_size))
                if progress_callback:
                    progress_callback(24 + int(ratio * 43), f'Lendo operações SICOR — {records_read:,} linhas'.replace(',', '.'))

        if raw_rows:
            _copy_batch(staging, _RAW_STAGE_TABLE, raw_copy_columns, raw_rows)
            _copy_batch(staging, _TYPED_STAGE_TABLE, typed_copy_columns, typed_rows)
        if suffix == '.gz':
            expanded_bytes = int(raw_binary.tell())
            expansion_limit = int(getattr(settings, 'MAX_ZIP_UNCOMPRESSED_BYTES', 0) or 0)
            if expansion_limit and expanded_bytes > expansion_limit:
                raise SecurityValidationError('O conteúdo descompactado excede o limite configurado.')
            compressed_bytes = max(1, int(path.stat().st_size))
            compression_ratio = expanded_bytes / compressed_bytes
            ratio_limit = int(getattr(settings, 'MAX_ZIP_EXPANSION_RATIO', 0) or 0)
            if ratio_limit and compression_ratio > ratio_limit:
                raise SecurityValidationError('O GZIP possui taxa de expansão acima do limite configurado.')
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise SecurityValidationError('O arquivo GZIP está corrompido ou incompleto.') from exc
    except csv.Error as exc:
        raise SicorOperationsValidationError(
            f'Erro ao ler linha {max(2, reader.line_num)} do arquivo SICOR.'
        ) from exc
    finally:
        text_stream.close()

    return {
        'registros_lidos': records_read,
        'registros_importados': records_imported,
        'registros_rejeitados': records_rejected,
        'amostras_rejeitadas': rejected_samples,
        'bytes_descompactados': expanded_bytes if suffix == '.gz' else file_size,
        'taxa_expansao': round(compression_ratio, 2) if compression_ratio is not None else None,
    }


def _create_raw_target(schema, raw_table, canonical_columns):
    raw_relation = _relation(schema, raw_table)
    definitions = [
        sql.SQL('_id bigserial PRIMARY KEY'),
        sql.SQL('_numero_linha bigint NOT NULL'),
        sql.SQL('_ano_arquivo integer NOT NULL'),
        sql.SQL('_arquivo_origem text NOT NULL'),
        *[sql.SQL('{} text').format(sql.Identifier(column)) for column in canonical_columns],
    ]
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL('CREATE TABLE IF NOT EXISTS {} ({})').format(
                raw_relation,
                sql.SQL(', ').join(definitions),
            )
        )
        for column in canonical_columns:
            cursor.execute(
                sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} text').format(
                    raw_relation, sql.Identifier(column),
                )
            )
        cursor.execute(
            sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} (_ano_arquivo)').format(
                sql.Identifier('raw_sicor_operacoes_ano_idx'), raw_relation,
            )
        )


def _publish_year(schema, table, raw_table, staging, columns_and_fields, year, filename):
    canonical_columns = [column for column, _field in columns_and_fields]
    raw_target_columns = ['_numero_linha', '_ano_arquivo', '_arquivo_origem', *canonical_columns]
    typed_source_columns = ['_numero_linha', '_ano_arquivo', *canonical_columns]
    typed_target_columns = [
        '_numero_linha', '_ano_arquivo',
        *[field.column for _column, field in columns_and_fields],
    ]

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT pg_advisory_xact_lock(hashtext(%s))',
                [f'confronta:{schema}:{table}:{year}'],
            )
            cursor.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))
        _create_raw_target(schema, raw_table, canonical_columns)
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL('DELETE FROM {} WHERE _ano_arquivo = %s').format(_relation(schema, raw_table)),
                [year],
            )
            cursor.execute(
                sql.SQL('INSERT INTO {} ({}) SELECT _numero_linha, _ano_arquivo, %s, {} FROM {} ORDER BY _numero_linha').format(
                    _relation(schema, raw_table),
                    sql.SQL(', ').join(sql.Identifier(column) for column in raw_target_columns),
                    sql.SQL(', ').join(sql.Identifier(column) for column in canonical_columns),
                    _relation(staging, _RAW_STAGE_TABLE),
                ),
                [filename],
            )
            cursor.execute(
                sql.SQL('DELETE FROM {} WHERE _ano_arquivo = %s').format(_relation(schema, table)),
                [year],
            )
            cursor.execute(
                sql.SQL('INSERT INTO {} ({}) SELECT {} FROM {} ORDER BY _numero_linha').format(
                    _relation(schema, table),
                    sql.SQL(', ').join(sql.Identifier(column) for column in typed_target_columns),
                    sql.SQL(', ').join(sql.Identifier(column) for column in typed_source_columns),
                    _relation(staging, _TYPED_STAGE_TABLE),
                )
            )
            cursor.execute(
                sql.SQL('SELECT COUNT(*) FROM {} WHERE _ano_arquivo = %s').format(_relation(schema, raw_table)),
                [year],
            )
            raw_count = int(cursor.fetchone()[0] or 0)
            cursor.execute(
                sql.SQL('SELECT COUNT(*) FROM {} WHERE _ano_arquivo = %s').format(_relation(schema, table)),
                [year],
            )
            operational_count = int(cursor.fetchone()[0] or 0)
    return raw_count, operational_count


def _progress(callback, percent, stage):
    if callback:
        try:
            callback(percent, stage)
        except BatchInterruptionRequested:
            raise
        except Exception:
            logger.exception('Falha ao publicar progresso de Operações SICOR.')


def _finish_failure(imp, status, reason, *, report=None, identity_status=None):
    imp.status = status
    imp.motivo_rejeicao = reason
    imp.data_finalizacao = timezone.now()
    if report is not None:
        imp.resultado = {'sicor_operacoes': report}
    if identity_status:
        imp.identidade_status = identity_status
    fields = ['status', 'motivo_rejeicao', 'data_finalizacao']
    if report is not None:
        fields.append('resultado')
    if identity_status:
        fields.append('identidade_status')
    imp.save(update_fields=fields)


def process_sicor_operations_import(uploaded_file, spec, usuario, context=None, progress_callback=None):
    """Stream SICOR operations into UNLOGGED staging and replace one year atomically."""
    started = time.monotonic()
    context = dict(context or {})
    filename = Path(uploaded_file.name).name
    year = operations_reference_year(filename)
    if year is None:
        # Normally rejected by the upload form; keep the service boundary safe.
        raise SicorOperationsValidationError('Ano SICOR inválido. Use um arquivo anual com ano de referência a partir de 2013.')

    imp = Importacao.objects.create(
        fonte=spec.fonte,
        dataset_slug=spec.slug,
        dataset_label=spec.label,
        nome_arquivo_original=filename,
        hash_sha256='0' * 64,
        tamanho_bytes=0,
        administrador=usuario,
        status=Importacao.Status.RECEBIDO,
        contexto={**context, 'ano_referencia': year},
    )
    quarantine = None
    staging = None
    report = {
        'arquivo': filename,
        'ano': year,
        'registros_lidos': 0,
        'registros_importados': 0,
        'registros_rejeitados': 0,
        'destino': f'{FONTE_SCHEMAS[spec.fonte]}.{spec.stable_table}',
    }
    try:
        _progress(progress_callback, 3, 'Recebendo arquivo de Operações SICOR')
        max_upload = int(getattr(settings, 'MAX_UPLOAD_SIZE_BYTES', 0) or 0)
        if max_upload and uploaded_file.size > max_upload:
            raise SecurityValidationError('O arquivo excede o limite configurado para upload.')
        suffix = Path(filename).suffix.lower()
        if suffix not in {'', '.csv', '.gz'}:
            raise SicorOperationsValidationError('Formato não permitido para Operações SICOR.')

        quarantine, digest, size = _save_upload(uploaded_file, imp.pk)
        imp.hash_sha256 = digest
        imp.tamanho_bytes = size
        imp.quarantine_path = str(quarantine.relative_to(settings.BASE_DIR))
        imp.status = Importacao.Status.VALIDANDO
        imp.save(update_fields=['hash_sha256', 'tamanho_bytes', 'quarantine_path', 'status'])

        _progress(progress_callback, 12, 'Validando segurança e cabeçalho oficial')
        antivirus = run_antivirus(quarantine)
        validate_operations_header(quarantine, filename)
        columns_and_fields = _columns_and_types()
        imp.identidade_status = 'CONFIRMADO'
        imp.identidade_relatorio = {
            'status': 'CONFIRMADO',
            'dataset': spec.slug,
            'ano_referencia': year,
            'colunas': len(_OPERATIONS_HEADER),
            'cabecalho': list(_OPERATIONS_HEADER),
            'delimitador': ';',
        }
        imp.status = Importacao.Status.VALIDANDO_IDENTIDADE
        imp.save(update_fields=['identidade_status', 'identidade_relatorio', 'status'])

        staging = create_staging_schema(imp.pk)
        _create_staging_tables(staging, columns_and_fields)
        imp.status = Importacao.Status.IMPORTANDO
        imp.save(update_fields=['status'])
        _progress(progress_callback, 22, 'Carregando staging em streaming via PostgreSQL COPY')
        stats = _stream_copy(
            quarantine,
            filename,
            year,
            staging,
            columns_and_fields,
            size,
            progress_callback,
        )
        report.update(stats)
        report['antimalware'] = antivirus
        if stats['registros_lidos'] <= 0:
            raise SicorOperationsImportError('O arquivo não contém registros de operações.', report)
        if stats['registros_importados'] <= 0:
            raise SicorOperationsImportError(
                'Nenhuma linha possui os campos e tipos necessários para importação. Os dados anteriores foram preservados.',
                report,
            )

        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(_relation(staging, _RAW_STAGE_TABLE)))
            staged_raw = int(cursor.fetchone()[0] or 0)
            cursor.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(_relation(staging, _TYPED_STAGE_TABLE)))
            staged_typed = int(cursor.fetchone()[0] or 0)
        if staged_raw != stats['registros_lidos'] or staged_typed != stats['registros_importados']:
            raise SicorOperationsImportError(
                'A conferência do staging não corresponde às linhas lidas; os dados anteriores foram preservados.',
                report,
            )

        _progress(progress_callback, 73, f'Validado: {staged_typed:,} registros prontos para publicar'.replace(',', '.'))
        schema = FONTE_SCHEMAS[spec.fonte]
        signature = hashlib.sha256('\x1f'.join(_OPERATIONS_HEADER).encode('utf-8')).hexdigest()
        with transaction.atomic():
            raw_count, operational_count = _publish_year(
                schema,
                spec.stable_table,
                spec.raw_table,
                staging,
                columns_and_fields,
                year,
                filename,
            )
            report.update({
                'registros_raw_ano': raw_count,
                'registros_operacionais_ano': operational_count,
                'tempo_processamento_segundos': round(time.monotonic() - started, 2),
                'substituicao': 'SUBSTITUICAO_ATOMICA_POR_ANO',
                'destino': f'{schema}.{spec.stable_table}',
                'destino_raw': f'{schema}.{spec.raw_table}',
                'mensagem': (
                    f'Importação SICOR {year} concluída: {operational_count:,} registros importados; '
                    f'{stats["registros_rejeitados"]:,} rejeitados.'
                ).replace(',', '.'),
            })
            imp.status = Importacao.Status.CONCLUIDO
            imp.data_finalizacao = timezone.now()
            imp.motivo_rejeicao = ''
            imp.resultado = {'sicor_operacoes': report}
            imp.save(update_fields=['status', 'data_finalizacao', 'motivo_rejeicao', 'resultado'])
            _upsert_layer(spec, imp, signature)
            registrar_auditoria(
                usuario,
                'IMPORTACAO_SICOR_OPERACOES_CONCLUIDA',
                'Importacao',
                imp.pk,
                {
                    'ano': year,
                    'registros_lidos': stats['registros_lidos'],
                    'registros_importados': operational_count,
                    'registros_rejeitados': stats['registros_rejeitados'],
                },
            )
        _progress(progress_callback, 100, 'Importação de Operações SICOR concluída')
        return imp

    except SicorOperationsValidationError as exc:
        report['tempo_processamento_segundos'] = round(time.monotonic() - started, 2)
        _finish_failure(
            imp,
            Importacao.Status.REJEITADO_IDENTIDADE,
            str(exc),
            report=report,
            identity_status='NAO_CONFIRMADO',
        )
        return imp
    except SicorOperationsImportError as exc:
        report = exc.report or report
        report['tempo_processamento_segundos'] = round(time.monotonic() - started, 2)
        _finish_failure(imp, Importacao.Status.FALHOU, str(exc), report=report)
        return imp
    except SecurityValidationError as exc:
        report['tempo_processamento_segundos'] = round(time.monotonic() - started, 2)
        _finish_failure(imp, Importacao.Status.REJEITADO_SEGURANCA, str(exc), report=report)
        return imp
    except BatchInterruptionRequested as exc:
        report['tempo_processamento_segundos'] = round(time.monotonic() - started, 2)
        report['base_ativa_preservada'] = True
        _finish_failure(imp, Importacao.Status.INTERROMPIDO, str(exc), report=report)
        return imp
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        report['tempo_processamento_segundos'] = round(time.monotonic() - started, 2)
        _finish_failure(
            imp,
            Importacao.Status.REJEITADO_SEGURANCA,
            'O arquivo GZIP está corrompido ou incompleto; os dados anteriores foram preservados.',
            report=report,
        )
        return imp
    except Exception:
        logger.exception('Falha interna ao importar Operações SICOR (%s, ano %s).', imp.pk, year)
        report['tempo_processamento_segundos'] = round(time.monotonic() - started, 2)
        _finish_failure(
            imp,
            Importacao.Status.FALHOU,
            'Falha ao importar Operações SICOR. Os dados anteriores do ano foram preservados.',
            report=report,
        )
        return imp
    finally:
        if staging:
            try:
                drop_schema(staging)
            except Exception:
                logger.exception('Não foi possível remover o staging SICOR de Operações %s.', imp.pk)
        if quarantine and quarantine.exists():
            quarantine.unlink(missing_ok=True)
