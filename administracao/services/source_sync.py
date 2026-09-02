from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.files import File
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from psycopg import sql

from administracao.models import FonteSincronizacao, User
from administracao.datasets import get_dataset
from administracao.services.pipeline import process_import
from administracao.services.confronta_contract import ensure_confronta_analysis_contract

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {
    FonteSincronizacao.Status.AGUARDANDO,
    FonteSincronizacao.Status.VERIFICANDO,
    FonteSincronizacao.Status.BAIXANDO,
    FonteSincronizacao.Status.VALIDANDO,
    FonteSincronizacao.Status.IMPORTANDO,
}

IBAMA_DATASET = 'ibama-termos-embargo'
IBAMA_COLLECTOR_VERSION = 'bulk-dados-abertos-v0.4.2'
INCRA_SIGEF_DATASET = 'incra-sigef-parcelas'
INCRA_SNCI_DATASET = 'incra-snci-certificados'


def _touch(job, *, status=None, progress=None, stage=None, details=None, error=None):
    fields = ['ultima_atividade']
    job.ultima_atividade = timezone.now()
    if status is not None:
        job.status = status
        fields.append('status')
    if progress is not None:
        job.progresso = max(0, min(100, int(progress)))
        fields.append('progresso')
    if stage is not None:
        job.etapa = str(stage)[:180]
        fields.append('etapa')
    if details is not None:
        merged = dict(job.detalhes or {})
        merged.update(details)
        job.detalhes = merged
        fields.append('detalhes')
    if error is not None:
        job.erro = str(error)
        fields.append('erro')
    job.save(update_fields=list(dict.fromkeys(fields)))


def enqueue_sync(fonte_slug, dataset_slug, *, uf='', user=None, origem=FonteSincronizacao.Origem.MANUAL):
    uf = str(uf or '').strip().upper()
    active = FonteSincronizacao.objects.filter(
        fonte_slug=fonte_slug, dataset_slug=dataset_slug, uf=uf, status__in=ACTIVE_STATUSES,
    ).first()
    if active:
        return active, False
    try:
        with transaction.atomic():
            job = FonteSincronizacao.objects.create(
                fonte_slug=fonte_slug, dataset_slug=dataset_slug, uf=uf, origem=origem,
                solicitado_por=user, status=FonteSincronizacao.Status.AGUARDANDO,
                progresso=0, etapa='Aguardando na fila',
            )
        return job, True
    except IntegrityError:
        active = FonteSincronizacao.objects.filter(
            fonte_slug=fonte_slug, dataset_slug=dataset_slug, uf=uf, status__in=ACTIVE_STATUSES,
        ).order_by('-criado_em').first()
        if active:
            return active, False
        raise


def enqueue_ibama(*, user=None, origem=FonteSincronizacao.Origem.MANUAL):
    job, created = enqueue_sync('ibama', IBAMA_DATASET, user=user, origem=origem)
    if created:
        job.detalhes = {
            **(job.detalhes or {}),
            'estrategia_ibama': IBAMA_COLLECTOR_VERSION,
            'coletor': 'Dados Abertos IBAMA / CSV',
        }
        job.save(update_fields=['detalhes'])
    return job, created


def enqueue_incra_pe(*, user=None, origem=FonteSincronizacao.Origem.MANUAL):
    jobs = []
    created = 0
    for dataset_slug in (INCRA_SIGEF_DATASET, INCRA_SNCI_DATASET):
        job, was_created = enqueue_sync('incra', dataset_slug, uf='PE', user=user, origem=origem)
        jobs.append(job)
        created += int(was_created)
    return jobs, created


def _system_user():
    email = (getattr(settings, 'SOURCE_AUTOMATION_USER_EMAIL', '') or '').strip().lower()
    if email:
        user = User.objects.filter(email=email, is_active=True).first()
        if user:
            return user
    return User.objects.filter(is_superuser=True, is_active=True).order_by('id').first()


def _remote_head_signature(url, timeout=60):
    req = Request(url, method='HEAD', headers={'User-Agent': 'CONFRONTA-Manage/0.4'})
    try:
        with urlopen(req, timeout=timeout) as response:
            length = response.headers.get('Content-Length') or ''
            modified = response.headers.get('Last-Modified') or ''
            etag = response.headers.get('ETag') or ''
            metadata = {
                'content_length': int(length) if str(length).isdigit() else None,
                'last_modified': modified, 'etag': etag,
            }
            if not etag and not modified:
                return '', metadata
            raw = f'{length}|{modified}|{etag}'
            return hashlib.sha256(raw.encode()).hexdigest(), metadata
    except Exception:
        return '', {}


def _download(url, target: Path, job: FonteSincronizacao, start_progress=15, end_progress=48, update_job_bytes=True):
    target.parent.mkdir(parents=True, exist_ok=True)
    retries = max(1, int(getattr(settings, 'SOURCE_HTTP_RETRIES', 3)))
    backoff = max(0.2, float(getattr(settings, 'SOURCE_HTTP_RETRY_BACKOFF_SECONDS', 2)))
    last_exc = None
    for attempt in range(1, retries + 1):
        req = Request(url, headers={'User-Agent': 'CONFRONTA-Manage/0.4', 'Accept': '*/*'})
        try:
            downloaded = 0
            sha = hashlib.sha256()
            with urlopen(req, timeout=getattr(settings, 'SOURCE_DOWNLOAD_TIMEOUT_SECONDS', 7200)) as response, target.open('wb') as dst:
                total_raw = response.headers.get('Content-Length')
                total = int(total_raw) if total_raw and total_raw.isdigit() else 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk); sha.update(chunk); downloaded += len(chunk)
                    if update_job_bytes:
                        job.bytes_baixados = downloaded
                    if total:
                        ratio = min(1.0, downloaded / total)
                        job.progresso = int(start_progress + ratio * (end_progress - start_progress))
                    job.ultima_atividade = timezone.now()
                    fields = ['progresso', 'ultima_atividade']
                    if update_job_bytes:
                        fields.append('bytes_baixados')
                    job.save(update_fields=fields)
            return sha.hexdigest(), downloaded
        except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            if isinstance(exc, HTTPError) and exc.code not in {408,425,429,500,502,503,504}:
                raise
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f'Falha de download após {retries} tentativa(s): {url} — {last_exc}') from last_exc


def _latest_success(job):
    return FonteSincronizacao.objects.filter(
        fonte_slug=job.fonte_slug, dataset_slug=job.dataset_slug, uf=job.uf,
        status__in=[FonteSincronizacao.Status.CONCLUIDO, FonteSincronizacao.Status.SEM_ALTERACAO],
    ).exclude(pk=job.pk).order_by('-finalizado_em', '-pk').first()


def _finish_no_change(job, signature, details=None):
    job.status = FonteSincronizacao.Status.SEM_ALTERACAO
    job.progresso = 100
    job.etapa = 'Sem alteração — banco preservado'
    job.assinatura_remota = signature or job.assinatura_remota
    job.finalizado_em = timezone.now(); job.ultima_atividade = job.finalizado_em
    if details:
        merged = dict(job.detalhes or {}); merged.update(details); job.detalhes = merged
    job.save()


def _process_local_file(job, path: Path, dataset_slug: str, user, context=None):
    _touch(job, status=FonteSincronizacao.Status.VALIDANDO, progress=55, stage='Validando arquivo oficial')
    context = dict(context or {})
    context.update({'sincronizacao_id': job.pk, 'origem_automatica': True})
    def progress(percent, stage):
        mapped = 55 + int(max(0, min(100, percent)) * 0.40)
        _touch(job, status=FonteSincronizacao.Status.IMPORTANDO, progress=min(95, mapped), stage=stage)
    with path.open('rb') as raw:
        uploaded = File(raw, name=path.name)
        imp = process_import(uploaded, dataset_slug, user, context=context, progress_callback=progress)
    job.importacao = imp
    job.save(update_fields=['importacao'])
    return imp

def _operational_has_rows(schema, table, *, uf=None):
    with connection.cursor() as cursor:
        cursor.execute('SELECT to_regclass(%s)', [f'{schema}.{table}'])
        if cursor.fetchone()[0] is None:
            return False
        if uf:
            cursor.execute(
                'SELECT 1 FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name=%s',
                [schema, table, 'uf_origem'],
            )
            if cursor.fetchone():
                cursor.execute(
                    sql.SQL('SELECT 1 FROM {}.{} WHERE uf_origem=%s LIMIT 1').format(
                        sql.Identifier(schema), sql.Identifier(table)
                    ),
                    [str(uf).upper()],
                )
                return cursor.fetchone() is not None
        cursor.execute(
            sql.SQL('SELECT 1 FROM {}.{} LIMIT 1').format(
                sql.Identifier(schema), sql.Identifier(table)
            )
        )
        return cursor.fetchone() is not None


def _db_fingerprint_map(schema, table, key, columns, *, uf=None):
    """Fingerprint lógico por chave usando a própria tabela operacional.

    É usado apenas para métricas NOVO/ALTERADO/REMOVIDO. A publicação continua
    protegida pelo pipeline transacional e pela chave UNIQUE oficial.
    """
    with connection.cursor() as cursor:
        cursor.execute('SELECT to_regclass(%s)', [f'{schema}.{table}'])
        if cursor.fetchone()[0] is None:
            return {}
        cursor.execute(
            'SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s',
            [schema, table],
        )
        existing = {row[0] for row in cursor.fetchall()}
        if key not in existing:
            return {}
        usable = [column for column in columns if column in existing and column != key]
        parts = [sql.SQL("COALESCE({}::text,'')").format(sql.Identifier(column)) for column in usable]
        if 'geometry' in existing:
            parts.append(sql.SQL("COALESCE(encode(ST_AsEWKB(geometry),'hex'),'')"))
        if not parts:
            parts = [sql.SQL("''::text")]
        fingerprint = sql.SQL("md5(concat_ws(chr(31), {}))").format(sql.SQL(',').join(parts))
        query = sql.SQL('SELECT {key}::text, {fp} FROM {schema}.{table} WHERE {key} IS NOT NULL').format(
            key=sql.Identifier(key), fp=fingerprint,
            schema=sql.Identifier(schema), table=sql.Identifier(table),
        )
        params = []
        if uf and 'uf_origem' in existing:
            query += sql.SQL(' AND uf_origem=%s')
            params.append(str(uf).upper())
        cursor.execute(query, params)
        return {str(k): fp for k, fp in cursor.fetchall()}




def process_ibama_job(job: FonteSincronizacao):
    # Coleta em lote pelos recursos oficiais do portal Dados Abertos IBAMA.
    from administracao.services.ibama_bulk_sync import process_ibama_bulk_job
    return process_ibama_bulk_job(job)


def _incra_url(dataset_slug, uf):
    uf = str(uf or 'PE').upper()
    if dataset_slug == INCRA_SIGEF_DATASET:
        template = settings.INCRA_SIGEF_URL_TEMPLATE
    elif dataset_slug == INCRA_SNCI_DATASET:
        template = settings.INCRA_SNCI_URL_TEMPLATE
    else:
        raise ValueError('Dataset INCRA automático não suportado.')
    return template.format(uf=uf)


def process_incra_job(job: FonteSincronizacao):
    user = job.solicitado_por or _system_user()
    if not user:
        raise RuntimeError('Nenhum administrador ativo foi encontrado para registrar a importação automática.')
    uf = job.uf or 'PE'
    url = _incra_url(job.dataset_slug, uf)
    _touch(job, status=FonteSincronizacao.Status.VERIFICANDO, progress=5, stage=f'Verificando INCRA {uf}')
    head_signature, head = _remote_head_signature(url)
    previous = _latest_success(job)
    current_table = 'sigef_parcela' if job.dataset_slug == INCRA_SIGEF_DATASET else 'snci_imovel'
    if (
        head_signature and previous and previous.assinatura_remota == head_signature
        and _operational_has_rows('dados_incra', current_table, uf=uf)
    ):
        _finish_no_change(job, head_signature, {'remote_head': head, 'url': url})
        return

    if job.dataset_slug == INCRA_SIGEF_DATASET:
        suffix = 'SIGEF'
        table = 'sigef_parcela'
        key = 'parcela_co'
        fp_columns = (
            'rt', 'art', 'situacao', 'codigo_imovel', 'data_submissao',
            'data_aprovacao', 'status', 'nome_area', 'registro_matricula',
            'registro_data', 'codigo_municipio', 'codigo_uf',
        )
    else:
        suffix = 'SNCI'
        table = 'snci_imovel'
        key = 'num_certif'
        fp_columns = (
            'numero_processo', 'sr', 'data_certificacao', 'area_ha',
            'codigo_profissional', 'codigo_imovel', 'nome_imovel', 'uf',
        )
    old_fingerprints = _db_fingerprint_map(
        'dados_incra', table, key, fp_columns, uf=uf
    )

    with tempfile.TemporaryDirectory(prefix=f'confronta_incra_{uf.lower()}_') as tmp:
        target = Path(tmp) / f'{suffix}_{uf}.zip'
        _touch(job, status=FonteSincronizacao.Status.BAIXANDO, progress=12, stage=f'Baixando {suffix} — {uf}')
        content_hash, size = _download(url, target, job)
        signature = head_signature or content_hash
        job.assinatura_remota = signature
        job.bytes_baixados = size
        job.save(update_fields=['assinatura_remota', 'bytes_baixados'])

        # Se HEAD não estava disponível, o hash do arquivo ainda impede trabalho repetido.
        if (
            previous and previous.assinatura_remota == signature
            and _operational_has_rows('dados_incra', table, uf=uf)
        ):
            _finish_no_change(job, signature, {'url': url, 'sha256': content_hash})
            return

        imp = _process_local_file(job, target, job.dataset_slug, user, context={'uf': uf})
        if imp.status not in {imp.Status.CONCLUIDO, imp.Status.SEM_ALTERACAO, imp.Status.IGNORADO_DUPLICADO}:
            raise RuntimeError(imp.motivo_rejeicao or f'Importação INCRA terminou em {imp.get_status_display()}.')
        new_fingerprints = _db_fingerprint_map(
            'dados_incra', table, key, fp_columns, uf=uf
        )
        common = set(old_fingerprints) & set(new_fingerprints)
        job.novos = len(set(new_fingerprints) - set(old_fingerprints))
        job.removidos = len(set(old_fingerprints) - set(new_fingerprints))
        job.alterados = sum(
            1 for item_key in common
            if old_fingerprints[item_key] != new_fingerprints[item_key]
        )
        job.save(update_fields=['novos', 'alterados', 'removidos'])
        ensure_confronta_analysis_contract()
        result = imp.resultado or {}
        normalized = ((result.get('promocao') or {}).get('normalizacao') or result.get('normalizacao') or {})
        count = normalized.get('registros_inseridos_operacional')
        job.registros_fonte = int(count) if count is not None else len(new_fingerprints)
        job.save(update_fields=['registros_fonte'])

    job.status = FonteSincronizacao.Status.CONCLUIDO if imp.status == imp.Status.CONCLUIDO else FonteSincronizacao.Status.SEM_ALTERACAO
    job.progresso = 100
    job.etapa = f'{suffix} {uf} atualizado no PostGIS' if job.status == FonteSincronizacao.Status.CONCLUIDO else 'Sem alteração — banco preservado'
    job.finalizado_em = timezone.now()
    job.ultima_atividade = timezone.now()
    job.detalhes = {**(job.detalhes or {}), 'url': url, 'sha256': content_hash, 'remote_head': head}
    job.save()


def process_next_job():
    candidate = FonteSincronizacao.objects.filter(
        status=FonteSincronizacao.Status.AGUARDANDO
    ).order_by('criado_em', 'pk').values_list('pk', flat=True).first()
    if not candidate:
        return False
    now = timezone.now()
    claimed = FonteSincronizacao.objects.filter(
        pk=candidate, status=FonteSincronizacao.Status.AGUARDANDO
    ).update(
        status=FonteSincronizacao.Status.VERIFICANDO,
        etapa='Iniciando verificação',
        iniciado_em=now,
        ultima_atividade=now,
    )
    if not claimed:
        return True
    job = FonteSincronizacao.objects.get(pk=candidate)
    try:
        if job.fonte_slug == 'ibama' and job.dataset_slug == IBAMA_DATASET:
            process_ibama_job(job)
        elif job.fonte_slug == 'incra' and job.dataset_slug in {INCRA_SIGEF_DATASET, INCRA_SNCI_DATASET}:
            process_incra_job(job)
        else:
            raise RuntimeError('Sincronização automática não cadastrada para esta fonte/dataset.')
    except Exception as exc:
        logger.exception('Falha na sincronização automática #%s', job.pk)
        job.status = FonteSincronizacao.Status.FALHOU
        job.etapa = 'Falha durante atualização'
        job.erro = str(exc)
        job.finalizado_em = timezone.now()
        job.ultima_atividade = job.finalizado_em
        job.save()
    return True


def _already_scheduled_today(fonte_slug, dataset_slug, uf=''):
    today = timezone.localdate()
    return FonteSincronizacao.objects.filter(
        fonte_slug=fonte_slug,
        dataset_slug=dataset_slug,
        uf=uf,
        origem=FonteSincronizacao.Origem.AGENDADO,
        criado_em__date=today,
    ).exists()


def schedule_due_jobs(now=None):
    now = now or timezone.localtime()
    if getattr(settings, 'IBAMA_AUTOMATION_ENABLED', True):
        due = now.hour > settings.IBAMA_AUTOMATION_HOUR or (
            now.hour == settings.IBAMA_AUTOMATION_HOUR and now.minute >= settings.IBAMA_AUTOMATION_MINUTE
        )
        if due and not _already_scheduled_today('ibama', IBAMA_DATASET):
            enqueue_ibama(user=_system_user(), origem=FonteSincronizacao.Origem.AGENDADO)

    if getattr(settings, 'INCRA_AUTOMATION_ENABLED', True):
        due = now.hour > settings.INCRA_AUTOMATION_HOUR or (
            now.hour == settings.INCRA_AUTOMATION_HOUR and now.minute >= settings.INCRA_AUTOMATION_MINUTE
        )
        if due:
            for dataset_slug in (INCRA_SIGEF_DATASET, INCRA_SNCI_DATASET):
                # O catálogo unificado atual mantém SIGEF em fonte própria e não
                # agenda datasets legados que não estejam mais registrados.
                if get_dataset(dataset_slug) is None:
                    continue
                if not _already_scheduled_today('incra', dataset_slug, 'PE'):
                    enqueue_sync('incra', dataset_slug, uf='PE', user=_system_user(), origem=FonteSincronizacao.Origem.AGENDADO)
