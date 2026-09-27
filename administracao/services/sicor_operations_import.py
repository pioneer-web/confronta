from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
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
from .sicor_publication import comparison as compare_publication, count_rows, count_staging_rows, make_staging_durable
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
    content_fingerprint = hashlib.sha256()
    content_fingerprint.update(('\x1f'.join(_OPERATIONS_HEADER) + '\n').encode('utf-8'))
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

            content_fingerprint.update(
                (json.dumps(values, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
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
        'fingerprint_conteudo': content_fingerprint.hexdigest(),
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


def _publish_year(schema, table, raw_table, staging, columns_and_fields, year, filename, confirmation=None):
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
        base_count = count_rows(schema, table, year)
        new_count = count_staging_rows(staging, _TYPED_STAGE_TABLE, year=year)
        compared = compare_publication(
            base_count, new_count, year=year,
            policy='SUBSTITUICAO_ATOMICA_POR_ANO', preserve_years=True,
        )
        if compared['reducao_detectada']:
            approved_count = (confirmation or {}).get('quantidade_anterior')
            if not (confirmation or {}).get('confirmacao_manual') or approved_count != base_count:
                if confirmation and confirmation.get('confirmacao_manual'):
                    compared['confirmacao_expirada'] = True
                return None, None, compared
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
    return raw_count, operational_count, compared


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


def _publish_operations_stage(imp, spec, usuario, year, staging, filename, report, confirmation=None):
    schema = FONTE_SCHEMAS[spec.fonte]
    signature = hashlib.sha256('\x1f'.join(_OPERATIONS_HEADER).encode('utf-8')).hexdigest()
    raw_count, operational_count, compared = _publish_year(
        schema, spec.stable_table, spec.raw_table, staging,
        _columns_and_types(), year, filename, confirmation=confirmation,
    )
    if compared['reducao_detectada'] and raw_count is None:
        make_staging_durable(staging, [_RAW_STAGE_TABLE, _TYPED_STAGE_TABLE])
        report['comparacao_publicacao'] = compared
        report['staging_schema'] = staging
        report['politica'] = 'SUBSTITUICAO_ATOMICA_POR_ANO'
        report['anos_anteriores_preservados'] = True
        imp.status = Importacao.Status.AGUARDANDO_CONFIRMACAO_REDUCAO
        imp.motivo_rejeicao = 'Redução de registros detectada. A publicação automática foi bloqueada para evitar substituição por arquivo possivelmente incompleto.'
        imp.resultado = {'sicor_operacoes': report, 'comparacao_publicacao': compared}
        imp.save(update_fields=['status', 'motivo_rejeicao', 'resultado'])
        registrar_auditoria(usuario, 'SICOR_REDUCAO_PUBLICACAO_BLOQUEADA', 'Importacao', imp.pk, {
            'dataset': spec.slug, **compared, 'confirmacao_manual': False,
        })
        return imp

    confirmation = confirmation or {}
    comparison_data = {
        **compared,
        'confirmacao_manual': bool(confirmation.get('confirmacao_manual')),
        'usuario_confirmou_id': confirmation.get('usuario_id'),
        'confirmado_em': confirmation.get('confirmado_em'),
    }
    report.update({
        'registros_raw_ano': raw_count,
        'registros_operacionais_ano': operational_count,
        'comparacao_publicacao': comparison_data,
        'tempo_processamento_segundos': report.get('tempo_processamento_segundos'),
        'substituicao': 'SUBSTITUICAO_ATOMICA_POR_ANO',
        'politica': 'SUBSTITUICAO_ATOMICA_POR_ANO',
        'anos_anteriores_preservados': True,
        'destino': f'{schema}.{spec.stable_table}',
        'destino_raw': f'{schema}.{spec.raw_table}',
        'mensagem': (
            f'Importação SICOR {year} concluída: {operational_count:,} registros importados; '
            f'{report.get("registros_rejeitados", 0):,} rejeitados.'
        ).replace(',', '.'),
    })
    imp.status = Importacao.Status.CONCLUIDO
    imp.data_finalizacao = timezone.now()
    imp.motivo_rejeicao = ''
    imp.resultado = {'sicor_operacoes': report}
    imp.save(update_fields=['status', 'data_finalizacao', 'motivo_rejeicao', 'resultado'])
    _upsert_layer(spec, imp, signature)
    registrar_auditoria(usuario, 'IMPORTACAO_SICOR_OPERACOES_CONCLUIDA', 'Importacao', imp.pk, {
        'dataset': spec.slug, 'ano_referencia': year,
        'quantidade_anterior': compared['registros_base_atual'],
        'quantidade_nova': operational_count,
        'diferenca': compared['diferenca_registros'],
        'percentual': compared['diferenca_percentual'],
        'confirmacao_manual': comparison_data['confirmacao_manual'],
        'usuario_confirmou_id': comparison_data['usuario_confirmou_id'],
        'confirmado_em': comparison_data['confirmado_em'],
        'politica': 'SUBSTITUICAO_ATOMICA_POR_ANO',
        'anos_anteriores_preservados': True,
    })
    return imp


def resume_sicor_operations_import(importacao_id, usuario):
    with transaction.atomic():
        imp = Importacao.objects.select_for_update().get(pk=importacao_id)
        if imp.status != Importacao.Status.AGUARDANDO_CONFIRMACAO_REDUCAO and not (
            imp.status == Importacao.Status.IMPORTANDO
            and (imp.contexto or {}).get('sicor_reduction_confirmation', {}).get('confirmacao_manual')
        ) and not (
            imp.status == Importacao.Status.FALHOU
            and (imp.contexto or {}).get('sicor_reduction_confirmation', {}).get('confirmacao_manual')
            and (imp.resultado or {}).get('sicor_operacoes', {}).get('staging_schema')
        ):
            raise ValueError('Esta publicação SICOR não está aguardando confirmação.')
        from administracao.datasets import get_dataset
        spec = get_dataset(imp.dataset_slug)
        report = dict((imp.resultado or {}).get('sicor_operacoes') or {})
        staging = report.get('staging_schema')
        if not spec or not staging:
            raise ValueError('O staging desta publicação SICOR não está disponível para retomada.')
        if imp.status != Importacao.Status.IMPORTANDO:
            imp.status = Importacao.Status.IMPORTANDO
            imp.save(update_fields=['status'])
    confirmation = (imp.contexto or {}).get('sicor_reduction_confirmation') or {}
    try:
        with transaction.atomic():
            result = _publish_operations_stage(
                imp, spec, usuario, int(report['ano']), staging,
                report.get('arquivo') or imp.nome_arquivo_original, report, confirmation,
            )
        if result.status == Importacao.Status.CONCLUIDO:
            drop_schema(staging)
        elif (result.resultado or {}).get('comparacao_publicacao', {}).get('confirmacao_expirada'):
            context = dict(result.contexto or {})
            context.pop('sicor_reduction_confirmation', None)
            result.contexto = context
            result.save(update_fields=['contexto'])
        return result
    except Exception:
        logger.exception('Falha ao retomar publicação de Operações SICOR %s.', imp.pk)
        imp.refresh_from_db()
        if imp.status == Importacao.Status.IMPORTANDO:
            imp.status = Importacao.Status.AGUARDANDO_CONFIRMACAO_REDUCAO
            imp.save(update_fields=['status'])
        raise


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

        duplicate = Importacao.objects.filter(
            dataset_slug=spec.slug,
            hash_sha256=digest,
            status__in=[Importacao.Status.CONCLUIDO, Importacao.Status.SEM_ALTERACAO],
        ).exclude(pk=imp.pk).order_by('-data_inicio').first()
        if duplicate and int((duplicate.resultado or {}).get('sicor_operacoes', {}).get('ano') or 0) == year:
            imp.status = Importacao.Status.SEM_ALTERACAO
            imp.identidade_status = 'SEM_ALTERACAO'
            imp.data_finalizacao = timezone.now()
            imp.resultado = {
                'duplicado': True,
                'importacao_anterior_id': duplicate.pk,
                'sicor_operacoes': {'ano': year, 'motivo': 'SHA-256 idêntico à versão anual já publicada.'},
            }
            imp.save(update_fields=['status', 'identidade_status', 'data_finalizacao', 'resultado'])
            registrar_auditoria(usuario, 'IMPORTACAO_SICOR_OPERACOES_SEM_ALTERACAO', 'Importacao', imp.pk, {
                'ano_referencia': year, 'importacao_anterior_id': duplicate.pk,
                'motivo': 'SHA-256 idêntico à versão anual já publicada.',
            })
            return imp

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

        same_content = Importacao.objects.filter(
            dataset_slug=spec.slug,
            status__in=[Importacao.Status.CONCLUIDO, Importacao.Status.SEM_ALTERACAO],
            resultado__sicor_operacoes__ano=year,
            resultado__sicor_operacoes__fingerprint_conteudo=stats['fingerprint_conteudo'],
        ).exclude(pk=imp.pk).exists()
        if same_content:
            imp.status = Importacao.Status.SEM_ALTERACAO
            imp.data_finalizacao = timezone.now()
            imp.resultado = {
                'sem_alteracao': True,
                'sicor_operacoes': {
                    **stats, 'ano': year,
                    'motivo': 'Conteúdo operacional SICOR idêntico ao já publicado; nenhuma escrita foi realizada.',
                },
            }
            imp.save(update_fields=['status', 'data_finalizacao', 'resultado'])
            registrar_auditoria(usuario, 'IMPORTACAO_SICOR_OPERACOES_SEM_ALTERACAO', 'Importacao', imp.pk, {
                'dataset': spec.slug, 'ano_referencia': year,
                'fingerprint_conteudo': stats['fingerprint_conteudo'],
            })
            return imp

        _progress(progress_callback, 73, f'Validado: {staged_typed:,} registros prontos para publicar'.replace(',', '.'))
        report['tempo_processamento_segundos'] = round(time.monotonic() - started, 2)
        with transaction.atomic():
            _publish_operations_stage(
                imp, spec, usuario, year, staging, filename, report,
                (imp.contexto or {}).get('sicor_reduction_confirmation'),
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
        if staging and imp.status != Importacao.Status.AGUARDANDO_CONFIRMACAO_REDUCAO:
            try:
                drop_schema(staging)
            except Exception:
                logger.exception('Não foi possível remover o staging SICOR de Operações %s.', imp.pk)
        if quarantine and quarantine.exists():
            quarantine.unlink(missing_ok=True)
