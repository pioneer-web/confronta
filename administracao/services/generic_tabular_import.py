from __future__ import annotations

import csv
import gzip
import hashlib
import io
import re
import shutil
import zipfile
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from psycopg import sql

from administracao.constants import FONTE_SCHEMAS
from administracao.models import CamadaImportada, Importacao
from .auditoria import registrar_auditoria
from .content_fingerprint import fingerprint_staging_content
from .exceptions import BatchInterruptionRequested, DatasetIdentityError, SecurityValidationError
from .names import normalize_identifier
from .postgis import create_staging_schema, drop_schema, promote_dataset
from .zip_security import run_antivirus, validate_zip

_SUPPORTED_ENCODINGS = ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1')
_DELIMITERS = (';', ',', '\t', '|')
_GZIP_MAGIC = b'\x1f\x8b'


def _save_upload(uploaded_file, import_id: int):
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in {'.csv', '.gz', '.zip'}:
        suffix = '.bin'
    target = Path(settings.QUARANTINE_DIR) / f'rawtab_{import_id}{suffix}'
    digest = hashlib.sha256()
    size = 0
    with target.open('wb') as dst:
        for chunk in uploaded_file.chunks():
            size += len(chunk)
            digest.update(chunk)
            dst.write(chunk)
        dst.flush()
    return target, digest.hexdigest(), size


def _decode_sample(sample: bytes):
    for encoding in _SUPPORTED_ENCODINGS:
        try:
            return sample.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return sample.decode('utf-8', errors='replace'), 'utf-8-replace'


def _safe_headers(values):
    used = {'_id', '_numero_linha', '_arquivo_origem'}
    result = []
    original = []
    for idx, value in enumerate(values, 1):
        text = str(value or '').strip()
        original.append(text)
        base = normalize_identifier(text, prefix='campo') or f'campo_{idx}'
        name = base
        counter = 2
        while name in used:
            suffix = f'_{counter}'
            name = f'{base[:63-len(suffix)]}{suffix}'
            counter += 1
        used.add(name)
        result.append(name)
    return original, result


def _detect_format(path: Path):
    with path.open('rb') as fh:
        sample = fh.read(256 * 1024)
    text, encoding = _decode_sample(sample)
    scored = []
    for delimiter in _DELIMITERS:
        try:
            row = next(csv.reader(io.StringIO(text), delimiter=delimiter))
        except Exception:
            row = []
        scored.append((len(row), delimiter, row))
    scored.sort(reverse=True, key=lambda item: item[0])
    _score, delimiter, header = scored[0]
    if len(header) < 2:
        raise DatasetIdentityError(
            'Não foi possível identificar um cabeçalho tabular com pelo menos duas colunas.',
            {'status': 'NAO_CONFIRMADO', 'motivo': 'Cabeçalho CSV não reconhecido.'},
        )
    original, normalized = _safe_headers(header)
    return {
        'encoding': encoding,
        'delimiter': delimiter,
        'original_headers': original,
        'headers': normalized,
    }


def _prepare_csv(source: Path, import_id: int):
    workdir = Path(settings.EXTRACTED_DIR) / f'rawtab_{import_id}'
    workdir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    if suffix == '.csv':
        return source, workdir, {'formato': 'CSV'}
    if suffix == '.gz':
        with source.open('rb') as fh:
            if fh.read(2) != _GZIP_MAGIC:
                raise SecurityValidationError('O arquivo .gz não possui assinatura GZIP válida.')
        target = workdir / 'source.csv'
        expanded = 0
        limit = int(getattr(settings, 'MAX_ZIP_UNCOMPRESSED_BYTES', 0) or 0)
        try:
            with gzip.open(source, 'rb') as src, target.open('wb') as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    expanded += len(chunk)
                    if limit and expanded > limit:
                        raise SecurityValidationError('O CSV descompactado excede o limite configurado.')
                    dst.write(chunk)
        except (OSError, EOFError) as exc:
            raise SecurityValidationError(f'O arquivo GZIP está corrompido ou incompleto: {exc}') from exc
        if expanded == 0:
            raise SecurityValidationError('O arquivo GZIP está vazio.')
        return target, workdir, {'formato': 'GZIP/CSV', 'descompactado_bytes': expanded}
    if suffix == '.zip':
        security = validate_zip(source)
        with zipfile.ZipFile(source) as zf:
            csv_names = [name for name in zf.namelist() if not name.endswith('/') and Path(name).suffix.lower() == '.csv']
            if len(csv_names) != 1:
                raise SecurityValidationError(
                    f'O ZIP tabular deve conter exatamente um CSV; foram encontrados {len(csv_names)}.'
                )
            name = csv_names[0]
            target = workdir / 'source.csv'
            with zf.open(name) as src, target.open('wb') as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
        return target, workdir, {'formato': 'ZIP/CSV', **security, 'csv_interno': name}
    raise SecurityValidationError('Formato tabular não permitido. Use .csv, .gz ou .zip contendo um único CSV.')


def _create_staging_table(staging: str, table: str, headers: list[str]):
    relation = sql.SQL('{}.{}').format(sql.Identifier(staging), sql.Identifier(table))
    columns = [sql.SQL('{} text').format(sql.Identifier(name)) for name in headers]
    columns.extend([
        sql.SQL('_numero_linha bigint NOT NULL'),
        sql.SQL('_arquivo_origem text NOT NULL'),
    ])
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL('CREATE TABLE {} ({})').format(relation, sql.SQL(',').join(columns)))


def _copy_csv(csv_path: Path, csv_info, staging: str, table: str, file_name: str):
    relation = sql.SQL('{}.{}').format(sql.Identifier(staging), sql.Identifier(table))
    cols = list(csv_info['headers']) + ['_numero_linha', '_arquivo_origem']
    copy_sql = sql.SQL('COPY {} ({}) FROM STDIN').format(
        relation,
        sql.SQL(',').join(sql.Identifier(col) for col in cols),
    )
    csv.field_size_limit(max(csv.field_size_limit(), 32 * 1024 * 1024))
    rows = 0
    with csv_path.open('r', encoding=csv_info['encoding'], errors='replace', newline='') as handle:
        reader = csv.reader(handle, delimiter=csv_info['delimiter'])
        header = next(reader, None)
        if header is None:
            raise DatasetIdentityError('O CSV está vazio.', {'status': 'NAO_CONFIRMADO'})
        _original, actual_headers = _safe_headers(header)
        if actual_headers != csv_info['headers']:
            raise DatasetIdentityError('O cabeçalho mudou entre a inspeção e a leitura.', {'status': 'NAO_CONFIRMADO'})
        with connection.cursor() as cursor:
            with cursor.copy(copy_sql) as copy:
                for line_number, values in enumerate(reader, start=2):
                    if not values or all(not str(v).strip() for v in values):
                        continue
                    if len(values) < len(actual_headers):
                        values = list(values) + [''] * (len(actual_headers) - len(values))
                    elif len(values) > len(actual_headers):
                        raise DatasetIdentityError(
                            f'A linha {line_number} possui {len(values)} colunas, mas o cabeçalho possui {len(actual_headers)}.',
                            {'status': 'NAO_CONFIRMADO', 'linha': line_number},
                        )
                    copy.write_row([*values, line_number, file_name])
                    rows += 1
    return rows


def _previous_content_match(spec, current_id: int, fingerprint: dict):
    expected = str((fingerprint or {}).get('sha256') or '')
    if not expected:
        return None
    layer = CamadaImportada.objects.filter(fonte=spec.fonte, dataset_slug=spec.slug).first()
    if not layer or layer.status != CamadaImportada.Status.ATIVA:
        return None
    candidates = Importacao.objects.filter(
        dataset_slug=spec.slug,
        status=Importacao.Status.CONCLUIDO,
    ).exclude(pk=current_id).order_by('-data_inicio')[:20]
    for candidate in candidates:
        previous = (candidate.resultado or {}).get('fingerprint_conteudo') or {}
        if str(previous.get('sha256') or '') == expected:
            return candidate
    return None


def process_generic_tabular_import(uploaded_file, spec, usuario, context=None, progress_callback=None):
    context = dict(context or {})
    def progress(percent, stage):
        if progress_callback:
            progress_callback(percent, stage)

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
    staging = None
    quarantine = None
    workdir = None
    try:
        progress(5, 'Recebendo arquivo')
        if settings.MAX_UPLOAD_SIZE_BYTES and uploaded_file.size > settings.MAX_UPLOAD_SIZE_BYTES:
            raise SecurityValidationError('O arquivo excede o limite configurado para upload.')
        quarantine, digest, size = _save_upload(uploaded_file, imp.pk)
        imp.hash_sha256 = digest
        imp.tamanho_bytes = size
        imp.quarantine_path = str(quarantine.relative_to(settings.BASE_DIR))
        imp.status = Importacao.Status.VALIDANDO
        imp.save(update_fields=['hash_sha256','tamanho_bytes','quarantine_path','status'])

        active_layer = CamadaImportada.objects.filter(fonte=spec.fonte, dataset_slug=spec.slug).first()
        duplicate = Importacao.objects.filter(
            dataset_slug=spec.slug, hash_sha256=digest, status=Importacao.Status.CONCLUIDO
        ).exclude(pk=imp.pk).order_by('-data_inicio').first()
        if duplicate and (not active_layer or active_layer.status == CamadaImportada.Status.ATIVA):
            imp.status = Importacao.Status.IGNORADO_DUPLICADO
            imp.identidade_status = 'DUPLICADO'
            imp.data_finalizacao = timezone.now()
            imp.resultado = {'duplicado': True, 'importacao_anterior_id': duplicate.pk, 'motivo': 'SHA-256 idêntico.'}
            imp.save(update_fields=['status','identidade_status','data_finalizacao','resultado'])
            return imp

        progress(20, 'Validando contêiner e cabeçalho')
        antivirus = run_antivirus(quarantine)
        csv_path, workdir, container_info = _prepare_csv(quarantine, imp.pk)
        csv_info = _detect_format(csv_path)
        imp.status = Importacao.Status.VALIDANDO_IDENTIDADE
        imp.identidade_status = 'CONFIRMADO'
        imp.identidade_relatorio = {
            'status': 'CONFIRMADO',
            'dataset': spec.slug,
            'arquivo': imp.nome_arquivo_original,
            'formato': container_info.get('formato'),
            'encoding': csv_info['encoding'],
            'delimitador': repr(csv_info['delimiter']),
            'campos_recebidos': csv_info['headers'],
            'criterio_confirmacao': 'PERFIL_MANUAL_RAW_FLEXIVEL',
        }
        imp.save(update_fields=['status','identidade_status','identidade_relatorio'])

        progress(45, 'Carregando staging RAW')
        staging = create_staging_schema(imp.pk)
        _create_staging_table(staging, spec.raw_table, csv_info['headers'])
        rows = _copy_csv(csv_path, csv_info, staging, spec.raw_table, imp.nome_arquivo_original)
        fingerprint = fingerprint_staging_content(staging, spec.raw_table, None)
        previous_same = _previous_content_match(spec, imp.pk, fingerprint)
        if previous_same:
            imp.status = Importacao.Status.SEM_ALTERACAO
            imp.data_finalizacao = timezone.now()
            imp.resultado = {
                'sem_alteracao': True,
                'motivo': 'O conteúdo tabular é idêntico à última versão confirmada. Nenhuma escrita foi realizada no banco.',
                'importacao_anterior_id': previous_same.pk,
                'fingerprint_conteudo': fingerprint,
                'cabecalho': csv_info,
                'container': container_info,
                'antimalware': antivirus,
            }
            imp.save(update_fields=['status','data_finalizacao','resultado'])
            return imp

        progress(75, 'Publicando RAW flexível')
        signature = hashlib.sha256('|'.join(csv_info['headers']).encode('utf-8')).hexdigest()
        fake_layer = {
            'layer_name': Path(imp.nome_arquivo_original).stem,
            'signature': signature,
            'geometry_type': '',
            'epsg_detectado': None,
            'db_stats': {
                'registros': rows,
                'geometry_column': None,
                'tipo_geometria': '',
                'srid': None,
                'geometrias_invalidas': 0,
                'geometrias_vazias': 0,
                'geometrias_nulas': 0,
            },
        }
        promotion = promote_dataset(
            imp, fake_layer, staging, spec,
            raw_table_name=spec.raw_table,
            schema_drift={'changed': False, 'modo': 'RAW_FLEXIVEL'},
        )
        staging = None
        imp.status = Importacao.Status.CONCLUIDO
        imp.data_finalizacao = timezone.now()
        imp.resultado = {
            'raw_flexivel': True,
            'operacional_pendente_validacao': True,
            'fingerprint_conteudo': fingerprint,
            'cabecalho': csv_info,
            'container': container_info,
            'antimalware': antivirus,
            'totais': {'registros': rows},
            'promocao': promotion,
        }
        imp.save(update_fields=['status','data_finalizacao','resultado'])
        registrar_auditoria(usuario, 'IMPORTACAO_RAW_FLEXIVEL_CONCLUIDA', 'Importacao', imp.pk, {
            'fonte': str(spec.fonte), 'dataset': spec.slug, 'registros': rows,
        })
        progress(100, 'Concluído — RAW preservada')
        return imp
    except SecurityValidationError as exc:
        imp.status = Importacao.Status.REJEITADO_SEGURANCA
        imp.motivo_rejeicao = str(exc)
        imp.data_finalizacao = timezone.now()
        imp.save(update_fields=['status','motivo_rejeicao','data_finalizacao'])
        return imp
    except DatasetIdentityError as exc:
        imp.status = Importacao.Status.REJEITADO_IDENTIDADE
        imp.identidade_status = exc.report.get('status', 'NAO_CONFIRMADO')
        imp.identidade_relatorio = exc.report
        imp.motivo_rejeicao = str(exc)
        imp.data_finalizacao = timezone.now()
        imp.save(update_fields=['status','identidade_status','identidade_relatorio','motivo_rejeicao','data_finalizacao'])
        return imp
    except BatchInterruptionRequested as exc:
        imp.status = Importacao.Status.INTERROMPIDO
        imp.motivo_rejeicao = str(exc)
        imp.data_finalizacao = timezone.now()
        imp.resultado = {'interrompida': True, 'base_ativa_preservada': True, 'contexto': context}
        imp.save(update_fields=['status','motivo_rejeicao','data_finalizacao','resultado'])
        registrar_auditoria(usuario, 'IMPORTACAO_RAW_FLEXIVEL_INTERROMPIDA', 'Importacao', imp.pk, {
            'fonte': str(spec.fonte), 'dataset': spec.slug, 'base_ativa_preservada': True,
        })
        return imp

    except Exception as exc:
        imp.status = Importacao.Status.FALHOU
        imp.motivo_rejeicao = str(exc)
        imp.data_finalizacao = timezone.now()
        imp.save(update_fields=['status','motivo_rejeicao','data_finalizacao'])
        return imp
    finally:
        if staging:
            try:
                drop_schema(staging)
            except Exception:
                pass
        if workdir and Path(workdir).exists():
            shutil.rmtree(workdir, ignore_errors=True)
        if quarantine and Path(quarantine).exists():
            Path(quarantine).unlink(missing_ok=True)
