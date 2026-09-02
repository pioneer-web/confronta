import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path

import pyogrio
from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from administracao.models import (
    ItemLoteImportacao,
    LoteImportacao,
    SicarColetaAutomatica,
    SicarEstado,
    User,
)
from administracao.services.batch import calculate_batch_progress, create_batch_from_uploads
from administracao.services.partitioning import normalize_uf
from administracao.services.sicar_sources import (
    SICAR_FILE_BASENAMES, SICAR_SYNC_ORDER, dataset_is_current_today,
    direct_source_url, find_inbox_snapshot, source_description, source_is_automatic,
)
from administracao.services.sicar_portal_assisted import (
    archive_sidecar, read_snapshot_metadata, record_confirmed_version,
)

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {
    SicarColetaAutomatica.Status.AGUARDANDO_FILA,
    SicarColetaAutomatica.Status.BAIXANDO,
    SicarColetaAutomatica.Status.AGUARDANDO_IMPORTACAO,
    SicarColetaAutomatica.Status.IMPORTANDO,
}

_PROGRESS_TOKEN_RE = re.compile(r'(?<!\d)(\d{1,3})(?=(?:\.\.\.|%|\s*-\s*done))')


def _pilot_uf(uf=None):
    value = normalize_uf(uf or settings.SICAR_AUTOMATION_UF)
    if value != 'PE':
        raise ValueError('O piloto de coleta automática do SICAR está restrito a Pernambuco (PE).')
    return value


def _update_job_progress(job_id, *, percent=None, stage=None, bytes_downloaded=None, details=None):
    updates = {'ultima_atividade': timezone.now()}
    if percent is not None:
        updates['progresso_percentual'] = max(0, min(100, int(percent)))
    if stage is not None:
        updates['etapa'] = str(stage)[:180]
    if bytes_downloaded is not None:
        updates['bytes_baixados'] = max(0, int(bytes_downloaded))
    if details:
        job = SicarColetaAutomatica.objects.filter(pk=job_id).only('detalhes').first()
        if job:
            merged = dict(job.detalhes or {})
            merged.update(details)
            updates['detalhes'] = merged
    SicarColetaAutomatica.objects.filter(pk=job_id).update(**updates)


def enqueue_sicar_collection(*, usuario=None, origem=None, uf=None, data_agendada=None, dataset_slug='sicar-perimetros'):
    uf = _pilot_uf(uf)
    origem = origem or SicarColetaAutomatica.Origem.MANUAL
    existing = SicarColetaAutomatica.objects.filter(uf=uf, dataset_slug=dataset_slug, status__in=ACTIVE_STATUSES).order_by('-criado_em').first()
    if existing:
        return existing, False
    obj = SicarColetaAutomatica.objects.create(
        uf=uf,
        dataset_slug=dataset_slug,
        origem=origem,
        solicitado_por=usuario,
        data_agendada=data_agendada,
        progresso_percentual=0,
        etapa='Aguardando o coletor',
        detalhes={
            'fonte': source_description(dataset_slug, uf),
            'layer': settings.SICAR_WFS_LAYER_TEMPLATE.format(uf=uf.lower()) if dataset_slug == 'sicar-perimetros' else '',
            'piloto': True,
            'auto_confirmar': True,
        },
    )
    return obj, True



def enqueue_sicar_full_sync(*, usuario=None, origem=None, uf=None, data_agendada=None, now=None):
    """Monta uma sequência idempotente para as 9 camadas do piloto PE.

    Regra forte: camada validada hoje + partição presente no PostGIS é pulada antes
    de qualquer download. As demais só entram na fila se houver uma fonte realmente
    processável por máquina (WFS, URL direta configurada ou arquivo já presente na inbox).
    O portal protegido por CAPTCHA nunca é contornado silenciosamente.
    """
    uf = _pilot_uf(uf)
    now = now or timezone.now()
    queued, skipped_today, blocked_source, already_active = [], [], [], []
    for dataset_slug in SICAR_SYNC_ORDER:
        if dataset_is_current_today(uf, dataset_slug, now=now):
            skipped_today.append(dataset_slug)
            continue
        if not source_is_automatic(dataset_slug, uf):
            blocked_source.append(dataset_slug)
            continue
        job, created = enqueue_sicar_collection(
            usuario=usuario,
            origem=origem or SicarColetaAutomatica.Origem.MANUAL,
            uf=uf,
            data_agendada=data_agendada,
            dataset_slug=dataset_slug,
        )
        if created:
            queued.append(job)
        else:
            already_active.append(job)
    return {
        'queued': queued,
        'skipped_today': skipped_today,
        'blocked_source': blocked_source,
        'already_active': already_active,
    }

def enqueue_due_sicar_schedule(now=None):
    if not settings.SICAR_AUTOMATION_ENABLED:
        return None
    now = timezone.localtime(now or timezone.now())
    if now.hour < settings.SICAR_AUTOMATION_HOUR:
        return None
    if now.hour == settings.SICAR_AUTOMATION_HOUR and now.minute < settings.SICAR_AUTOMATION_MINUTE:
        return None
    today = now.date()

    # Um único plano automático por dia. Sem este marcador, camadas que ainda
    # dependem do portal protegido fariam o monitor reavaliar o mesmo plano a
    # cada 15 segundos depois das 01:00.
    state, _ = SicarEstado.objects.get_or_create(uf='PE')
    details = dict(state.detalhes or {})
    if details.get('ultima_rotina_agendada') == today.isoformat():
        return None

    result = enqueue_sicar_full_sync(
        origem=SicarColetaAutomatica.Origem.AGENDADA,
        uf='PE',
        data_agendada=today,
        now=now,
    )
    details['ultima_rotina_agendada'] = today.isoformat()
    details['ultima_rotina_agendada_em'] = now.isoformat()
    details['ultima_rotina_resumo'] = {
        'enfileiradas': len(result['queued']),
        'ignoradas_hoje': len(result['skipped_today']),
        'sem_fonte_automatica': len(result['blocked_source']),
        'ja_ativas': len(result['already_active']),
    }
    state.detalhes = details
    state.save(update_fields=['detalhes', 'atualizado_em'])
    return result

def recover_stale_sicar_collections(now=None):
    """Marca como falha uma coleta cujo processo foi interrompido.

    Durante download real o coletor atualiza `ultima_atividade` a cada poucos
    segundos. Assim uma coleta BAIXANDO sem atividade por muito tempo não fica
    eternamente presa na tela.
    """
    now = now or timezone.now()
    stale_seconds = max(300, int(getattr(settings, 'SICAR_STALE_SECONDS', 900)))
    cutoff = now - timedelta(seconds=stale_seconds)
    stale = SicarColetaAutomatica.objects.filter(status=SicarColetaAutomatica.Status.BAIXANDO).filter(
        ultima_atividade__lt=cutoff
    )
    count = 0
    for job in stale:
        job.status = SicarColetaAutomatica.Status.FALHOU
        job.finalizado_em = now
        job.etapa = 'Coleta interrompida'
        job.erro = 'A coleta ficou sem atividade e foi considerada interrompida. Execute uma nova verificação.'
        job.save(update_fields=['status', 'finalizado_em', 'etapa', 'erro'])
        count += 1
    # Compatibilidade com coletas que estavam BAIXANDO antes da migração e não
    # possuem heartbeat algum.
    null_activity = SicarColetaAutomatica.objects.filter(
        status=SicarColetaAutomatica.Status.BAIXANDO,
        ultima_atividade__isnull=True,
    )
    for job in null_activity:
        job.status = SicarColetaAutomatica.Status.FALHOU
        job.finalizado_em = now
        job.etapa = 'Coleta interrompida'
        job.erro = 'A coleta anterior não possui atividade registrada e foi encerrada para permitir uma nova tentativa.'
        job.save(update_fields=['status', 'finalizado_em', 'etapa', 'erro'])
        count += 1
    return count


def claim_next_sicar_collection():
    # A sequência SICAR é deliberadamente serial: só começamos a próxima camada
    # depois que a anterior terminou também a fase de análise/importação no PostGIS.
    # Isso evita download de 8 arquivos ao mesmo tempo e reduz pressão de CPU, disco e DB.
    inflight = SicarColetaAutomatica.objects.filter(
        status__in={
            SicarColetaAutomatica.Status.BAIXANDO,
            SicarColetaAutomatica.Status.AGUARDANDO_IMPORTACAO,
            SicarColetaAutomatica.Status.IMPORTANDO,
        }
    ).exists()
    if inflight:
        return None

    with transaction.atomic():
        job = (
            SicarColetaAutomatica.objects.select_for_update(skip_locked=True)
            .filter(status=SicarColetaAutomatica.Status.AGUARDANDO_FILA)
            .order_by('id')
            .first()
        )
        if not job:
            return None
        now = timezone.now()
        job.status = SicarColetaAutomatica.Status.BAIXANDO
        job.iniciado_em = now
        job.ultima_atividade = now
        job.progresso_percentual = 1
        job.etapa = 'Conectando ao SICAR oficial'
        job.bytes_baixados = 0
        job.erro = ''
        job.save(update_fields=[
            'status', 'iniciado_em', 'ultima_atividade', 'progresso_percentual',
            'etapa', 'bytes_baixados', 'erro',
        ])
        return job

def _resolve_user(job):
    if job.solicitado_por_id:
        user = User.objects.filter(pk=job.solicitado_por_id, is_active=True).first()
        if user:
            return user
    email = settings.SICAR_AUTOMATION_USER_EMAIL
    if email:
        user = User.objects.filter(email=email, is_active=True).first()
        if user:
            return user
    user = User.objects.filter(is_superuser=True, is_active=True).order_by('id').first()
    if not user:
        raise RuntimeError('Nenhum superadministrador ativo foi encontrado para auditar a coleta SICAR automática.')
    return user


def _remote_feature_count(uf):
    """Obtém contagem via WFS resultType=hits; falha aqui não bloqueia o download."""
    layer = settings.SICAR_WFS_LAYER_TEMPLATE.format(uf=uf.lower())
    params = urllib.parse.urlencode({
        'service': 'WFS',
        'version': '2.0.0',
        'request': 'GetFeature',
        'typeNames': layer,
        'resultType': 'hits',
    })
    separator = '&' if '?' in settings.SICAR_WFS_URL else '?'
    url = f'{settings.SICAR_WFS_URL}{separator}{params}'
    req = urllib.request.Request(url, headers={'User-Agent': 'CONFRONTA-Manage/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read(128 * 1024)
        root = ET.fromstring(data)
        value = root.attrib.get('numberMatched') or root.attrib.get('numberOfFeatures')
        if value and str(value).isdigit():
            return int(value)
    except Exception:
        logger.warning('Não foi possível obter a contagem WFS de %s antes do download.', layer, exc_info=True)
    return None


def _run_ogr2ogr(target, uf, *, job_id):
    layer = settings.SICAR_WFS_LAYER_TEMPLATE.format(uf=uf.lower())
    source = f'WFS:{settings.SICAR_WFS_URL}?service=WFS&version=2.0.0'
    target_layer = f'AREA_IMOVEL_{uf}'

    _update_job_progress(job_id, percent=2, stage='Consultando quantidade de imóveis no SICAR')
    remote_count = _remote_feature_count(uf)
    if remote_count is not None:
        _update_job_progress(
            job_id,
            percent=3,
            stage=f'Preparando download de {remote_count:,} imóveis'.replace(',', '.'),
            details={'registros_remotos': remote_count},
        )
    else:
        _update_job_progress(job_id, percent=3, stage='Preparando download do WFS oficial')

    cmd = [
        'ogr2ogr',
        '--config', 'OGR_WFS_PAGING_ALLOWED', 'ON',
        '--config', 'OGR_WFS_PAGE_SIZE', str(max(100, settings.SICAR_WFS_PAGE_SIZE)),
        '--config', 'OGR_WFS_USE_STREAMING', 'YES',
        '--config', 'GDAL_HTTP_MAX_RETRY', '5',
        '--config', 'GDAL_HTTP_RETRY_DELAY', '5',
        '--config', 'CPL_CURL_GZIP', 'YES',
        '-f', 'GPKG',
        '-overwrite',
        '-progress',
        '-nln', target_layer,
        # O WFS do SICAR pode anunciar/entregar MultiSurface. O GeoPackage do
        # Manage é normalizado para Simple Features antes da análise/importação.
        '-nlt', 'CONVERT_TO_LINEAR',
        '-nlt', 'PROMOTE_TO_MULTI',
        str(target),
        source,
        layer,
    ]
    env = os.environ.copy()
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    shared = {'progress': None, 'log': '', 'done': False}
    lock = threading.Lock()

    def reader():
        tail = ''
        parts = []
        try:
            while True:
                chunk = process.stdout.read(1) if process.stdout else ''
                if not chunk:
                    break
                parts.append(chunk)
                tail = (tail + chunk)[-512:]
                matches = list(_PROGRESS_TOKEN_RE.finditer(tail))
                if matches:
                    value = max(0, min(100, int(matches[-1].group(1))))
                    with lock:
                        if shared['progress'] is None or value > shared['progress']:
                            shared['progress'] = value
                if len(parts) > 12000:
                    parts = parts[-8000:]
        finally:
            with lock:
                shared['log'] = ''.join(parts)[-12000:]
                shared['done'] = True

    thread = threading.Thread(target=reader, name=f'sicar-gdal-progress-{job_id}', daemon=True)
    thread.start()

    started = time.monotonic()
    deadline = started + max(300, settings.SICAR_WFS_TIMEOUT_SECONDS)
    last_size = -1
    last_percent = -1
    try:
        while process.poll() is None:
            if time.monotonic() > deadline:
                process.kill()
                raise RuntimeError('O download do SICAR excedeu o tempo máximo configurado.')
            size = target.stat().st_size if target.exists() else 0
            with lock:
                gdal_progress = shared['progress']
            # 5% a 70% representam a transferência WFS. O restante fica reservado
            # para validação, análise e promoção ao PostGIS.
            overall = 5 if gdal_progress is None else 5 + round(gdal_progress * 0.65)
            if size != last_size or overall != last_percent:
                stage = 'Baixando perímetros do SICAR PE'
                if gdal_progress is not None:
                    stage += f' — {gdal_progress}% da transferência'
                _update_job_progress(
                    job_id,
                    percent=overall,
                    stage=stage,
                    bytes_downloaded=size,
                    details={'gdal_percentual': gdal_progress} if gdal_progress is not None else None,
                )
                last_size = size
                last_percent = overall
            time.sleep(2)
    finally:
        thread.join(timeout=3)

    returncode = process.wait(timeout=10)
    with lock:
        output = shared['log']
    if returncode != 0:
        stderr = (output or '').strip()
        raise RuntimeError(f'GDAL/ogr2ogr não conseguiu coletar {layer}: {stderr[-4000:]}')

    final_size = target.stat().st_size if target.exists() else 0
    _update_job_progress(
        job_id,
        percent=70,
        stage='Download concluído; validando GeoPackage',
        bytes_downloaded=final_size,
        details={'registros_remotos': remote_count} if remote_count is not None else None,
    )
    return layer, target_layer, (output or '').strip()


def _validate_snapshot(path, expected_layer):
    if not path.exists() or path.stat().st_size < 4096:
        raise RuntimeError('O GeoPackage recebido do SICAR está vazio ou incompleto.')
    layers = pyogrio.list_layers(path)
    layer_names = {str(row[0]) for row in layers}
    if expected_layer not in layer_names:
        raise RuntimeError(f'A camada esperada {expected_layer} não foi encontrada no GeoPackage.')
    info = pyogrio.read_info(path, layer=expected_layer)
    features = int(info.get('features') or 0)
    if features < 1:
        raise RuntimeError('A camada de perímetros do SICAR PE não contém feições.')
    raw_fields = info.get('fields')
    if raw_fields is None:
        raw_fields = []
    fields = {str(value).lower() for value in raw_fields}
    if 'cod_imovel' not in fields:
        raise RuntimeError('O campo obrigatório cod_imovel não foi encontrado na resposta do SICAR.')
    return {
        'camada': expected_layer,
        'registros_reportados': features,
        'geometry_type': str(info.get('geometry_type') or ''),
        'crs': str(info.get('crs') or ''),
        'tamanho_bytes': path.stat().st_size,
    }



def _download_direct_snapshot(job, url, target):
    """Baixa somente uma URL oficial configurada; não resolve/burla CAPTCHA."""
    req = urllib.request.Request(url, headers={'User-Agent': 'CONFRONTA-Manage/1.0'})
    _update_job_progress(job.pk, percent=5, stage=f'Baixando {job.dataset_slug} do SICAR')
    downloaded = 0
    with urllib.request.urlopen(req, timeout=max(60, settings.SICAR_WFS_TIMEOUT_SECONDS)) as response, target.open('wb') as out:
        length = response.headers.get('Content-Length')
        try:
            total = int(length) if length else 0
        except (TypeError, ValueError):
            total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            percent = 5 + round((downloaded / total) * 65) if total else 10
            _update_job_progress(
                job.pk, percent=min(70, percent), bytes_downloaded=downloaded,
                stage=f'Baixando {SICAR_FILE_BASENAMES.get(job.dataset_slug, job.dataset_slug)} — {downloaded // (1024*1024)} MB',
            )
    if not target.exists() or target.stat().st_size < 4096:
        raise RuntimeError('O arquivo recebido do SICAR está vazio ou incompleto.')
    _update_job_progress(job.pk, percent=70, stage='Download concluído; preparando análise', bytes_downloaded=target.stat().st_size)
    return target


def _archive_inbox_snapshot(path, dataset_slug, uf):
    """Move snapshot + metadado assistido para histórico da inbox."""
    path = Path(path)
    root = Path(settings.SICAR_AUTO_INBOX).resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None
    archive_dir = root / 'processados' / timezone.localdate().isoformat()
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / resolved.name
    if destination.exists():
        stamp = timezone.now().strftime('%H%M%S')
        destination = archive_dir / f'{resolved.stem}_{stamp}{resolved.suffix}'
    shutil.move(str(resolved), str(destination))
    archive_sidecar(resolved, archive_dir, destination.name)
    return destination


def _submit_snapshot_to_manage(job, snapshot_path, user, *, source_label):
    file_name = snapshot_path.name
    _update_job_progress(job.pk, percent=76, stage=f'Criando lote para {file_name}')
    with snapshot_path.open('rb') as raw:
        django_file = File(raw, name=file_name)
        lote = create_batch_from_uploads([django_file], 'sicar', user, default_uf=job.uf)
    if lote.status == LoteImportacao.Status.FALHOU:
        raise RuntimeError(lote.motivo_falha or 'O Manage não conseguiu criar o lote da coleta SICAR.')
    result = dict(lote.resultado or {})
    result.update({
        'modo': 'SICAR_AUTOMATICO',
        'automatico': True,
        'auto_confirmar': True,
        'coleta_sicar_id': job.pk,
        'dataset_slug_solicitado': job.dataset_slug,
        'fonte_remota': source_label,
    })
    lote.resultado = result
    lote.save(update_fields=['resultado'])
    details = dict(job.detalhes or {})
    details.update({'lote_id': lote.pk, 'arquivo': file_name, 'fonte': source_label})
    job.lote = lote
    job.status = SicarColetaAutomatica.Status.AGUARDANDO_IMPORTACAO
    job.progresso_percentual = 80
    job.etapa = 'Analisando alterações antes de importar'
    job.ultima_atividade = timezone.now()
    job.detalhes = details
    job.save(update_fields=['lote','status','progresso_percentual','etapa','ultima_atividade','detalhes'])
    return job

def process_sicar_collection(job):
    job = SicarColetaAutomatica.objects.get(pk=job.pk)
    if job.status != SicarColetaAutomatica.Status.BAIXANDO:
        return job

    # Regra idempotente de primeira linha. Para WFS/rotas sem uma nova evidência
    # remota, uma segunda execução no mesmo dia é descartada antes do download.
    # Exceção: se o assistente acabou de colocar um arquivo com metadado do portal
    # na inbox, ele precisa ser comparado pelo fingerprint mesmo que outra versão
    # tenha sido validada mais cedo no mesmo dia. Isso cobre atualização intradiária.
    assisted_inbox = find_inbox_snapshot(job.dataset_slug, job.uf) if job.dataset_slug != 'sicar-perimetros' else None
    assisted_meta = read_snapshot_metadata(assisted_inbox) if assisted_inbox else {}
    has_new_portal_evidence = bool(assisted_meta.get('remote_update_date') or assisted_meta.get('download_url'))
    if dataset_is_current_today(job.uf, job.dataset_slug) and not has_new_portal_evidence:
        now = timezone.now()
        job.status = SicarColetaAutomatica.Status.SEM_ALTERACAO
        job.finalizado_em = now
        job.ultima_atividade = now
        job.progresso_percentual = 100
        job.etapa = 'Já atualizado hoje — download dispensado'
        details = dict(job.detalhes or {})
        details['pulou_download'] = True
        details['motivo'] = 'Camada já validada hoje e confirmada no PostGIS.'
        job.detalhes = details
        job.save(update_fields=['status','finalizado_em','ultima_atividade','progresso_percentual','etapa','detalhes'])
        return job

    workdir = Path(settings.SICAR_AUTO_DIR) / f'coleta_{job.pk}'
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        user = _resolve_user(job)
        if job.dataset_slug == 'sicar-perimetros':
            partial = workdir / 'AREA_IMOVEL_PE.partial.gpkg'
            final = workdir / 'AREA_IMOVEL_PE.gpkg'
            partial.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            source_layer, target_layer, ogr_log = _run_ogr2ogr(partial, job.uf, job_id=job.pk)
            partial.replace(final)
            _update_job_progress(job.pk, percent=72, stage='Validando estrutura e atributos do SICAR')
            metadata = _validate_snapshot(final, target_layer)
            job.refresh_from_db()
            details = dict(job.detalhes or {})
            details.update(metadata)
            details['camada_remota'] = source_layer
            if ogr_log:
                details['gdal'] = ogr_log[-1500:]
            job.detalhes = details
            job.save(update_fields=['detalhes'])
            result = _submit_snapshot_to_manage(job, final, user, source_label=settings.SICAR_WFS_URL)
            shutil.rmtree(workdir, ignore_errors=True)
            return result

        inbox = find_inbox_snapshot(job.dataset_slug, job.uf)
        url = direct_source_url(job.dataset_slug)
        if inbox:
            _update_job_progress(job.pk, percent=70, stage='Arquivo oficial localizado na caixa de entrada', bytes_downloaded=inbox.stat().st_size)
            portal_meta = read_snapshot_metadata(inbox)
            if portal_meta:
                details = dict(job.detalhes or {})
                details.update({
                    'portal_remote_update_date': str(portal_meta.get('remote_update_date') or ''),
                    'portal_download_url': str(portal_meta.get('download_url') or ''),
                    'portal_source_page': str(portal_meta.get('source_page') or ''),
                    'portal_captured_at': str(portal_meta.get('captured_at') or ''),
                    'portal_human_captcha': bool(portal_meta.get('human_captcha')),
                })
                job.detalhes = details
                job.save(update_fields=['detalhes'])
            result = _submit_snapshot_to_manage(job, inbox, user, source_label=str(inbox))
            archived = _archive_inbox_snapshot(inbox, job.dataset_slug, job.uf)
            if archived:
                job.refresh_from_db()
                details = dict(job.detalhes or {})
                details['arquivo_inbox_arquivado'] = str(archived)
                job.detalhes = details
                job.save(update_fields=['detalhes'])
            shutil.rmtree(workdir, ignore_errors=True)
            return result
        if url:
            base = SICAR_FILE_BASENAMES.get(job.dataset_slug, job.dataset_slug.replace('sicar-', '').upper())
            final = workdir / f'{base}_{job.uf}.gpkg'
            _download_direct_snapshot(job, url, final)
            result = _submit_snapshot_to_manage(job, final, user, source_label=url)
            shutil.rmtree(workdir, ignore_errors=True)
            return result

        # Não inventamos automação onde a fonte oficial exige CAPTCHA. Isso evita
        # quebrar a política do portal e evita que o Manage marque um download inexistente.
        now = timezone.now()
        job.status = SicarColetaAutomatica.Status.ATENCAO
        job.finalizado_em = now
        job.ultima_atividade = now
        job.progresso_percentual = 100
        job.etapa = 'Fonte automática indisponível para esta camada'
        job.erro = 'O portal oficial exige validação humana e não há URL direta oficial configurada. Nenhum CAPTCHA foi contornado.'
        job.save(update_fields=['status','finalizado_em','ultima_atividade','progresso_percentual','etapa','erro'])
        return job
    except Exception as exc:
        logger.exception('Falha na coleta automática SICAR #%s (%s)', job.pk, job.dataset_slug)
        job.refresh_from_db()
        job.status = SicarColetaAutomatica.Status.FALHOU
        job.finalizado_em = timezone.now()
        job.ultima_atividade = timezone.now()
        job.etapa = 'Falha na coleta'
        job.erro = str(exc)[:8000]
        details = dict(job.detalhes or {})
        details['workdir_preservado'] = str(workdir)
        job.detalhes = details
        job.save(update_fields=['status','finalizado_em','ultima_atividade','etapa','erro','detalhes'])
        return job

def sync_sicar_collection_jobs():
    jobs = SicarColetaAutomatica.objects.filter(
        status__in={
            SicarColetaAutomatica.Status.AGUARDANDO_IMPORTACAO,
            SicarColetaAutomatica.Status.IMPORTANDO,
            # Permite que um job automático já marcado como falho volte a ser
            # acompanhado quando o lote é reenfileirado pela tela de Histórico.
            SicarColetaAutomatica.Status.FALHOU,
        },
        lote__isnull=False,
    ).select_related('lote')
    updated = 0
    for job in jobs:
        lote = job.lote
        # Um job já finalizado como FALHOU só volta a ser acompanhado se o lote
        # tiver sido realmente reenfileirado. Isso evita exibir "Atividade agora"
        # indefinidamente em uma falha encerrada.
        if job.status == SicarColetaAutomatica.Status.FALHOU and lote.status in {
            LoteImportacao.Status.CONCLUIDO,
            LoteImportacao.Status.CONCLUIDO_COM_PENDENCIAS,
            LoteImportacao.Status.FALHOU,
        }:
            continue
        new_status = job.status
        final = False
        progress = job.progresso_percentual
        stage = job.etapa
        batch_progress = calculate_batch_progress(lote)
        phase = str((lote.resultado or {}).get('fase') or 'ANALISE').upper()

        if lote.status in {LoteImportacao.Status.ANALISANDO, LoteImportacao.Status.AGUARDANDO_CONFIRMACAO}:
            new_status = SicarColetaAutomatica.Status.AGUARDANDO_IMPORTACAO
            progress = 80 + round(batch_progress * 0.10)
            stage = 'Analisando diferenças em relação à base atual'
        elif lote.status == LoteImportacao.Status.PROCESSANDO:
            new_status = SicarColetaAutomatica.Status.IMPORTANDO
            if phase == 'IMPORTACAO':
                progress = 90 + round(batch_progress * 0.09)
                stage = 'Importando alterações no PostGIS'
            else:
                progress = 80 + round(batch_progress * 0.10)
                stage = 'Analisando dados recebidos'
        elif lote.status == LoteImportacao.Status.CONCLUIDO:
            expected_items = lote.itens.filter(dataset_slug=job.dataset_slug)
            changed = expected_items.filter(status=ItemLoteImportacao.Status.CONCLUIDO).exists()
            unchanged = expected_items.filter(status__in={
                ItemLoteImportacao.Status.SEM_ALTERACAO,
                ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
            }).exists()
            if changed:
                new_status = SicarColetaAutomatica.Status.CONCLUIDO
                stage = 'Base atualizada no PostGIS'
            elif unchanged:
                new_status = SicarColetaAutomatica.Status.SEM_ALTERACAO
                stage = 'Verificação concluída sem alteração'
            else:
                new_status = SicarColetaAutomatica.Status.ATENCAO
                stage = 'Lote concluído sem confirmar a camada esperada'
                if not job.erro:
                    job.erro = f'O lote não confirmou o dataset esperado: {job.dataset_slug}.'
            progress = 100
            final = True
        elif lote.status == LoteImportacao.Status.CONCLUIDO_COM_PENDENCIAS:
            if lote.itens.filter(status=ItemLoteImportacao.Status.FALHOU).exists():
                new_status = SicarColetaAutomatica.Status.FALHOU
                stage = 'Falha durante análise/importação'
            else:
                new_status = SicarColetaAutomatica.Status.ATENCAO
                stage = 'Concluído com pendências'
                progress = 100
            final = True
        elif lote.status == LoteImportacao.Status.FALHOU:
            new_status = SicarColetaAutomatica.Status.FALHOU
            stage = 'Falha durante análise/importação'
            final = True

        fields = []
        if new_status != job.status:
            job.status = new_status
            fields.append('status')
        progress = max(0, min(100, int(progress or 0)))
        if progress != job.progresso_percentual:
            job.progresso_percentual = progress
            fields.append('progresso_percentual')
        if stage != job.etapa:
            job.etapa = stage[:180]
            fields.append('etapa')
        job.ultima_atividade = timezone.now()
        fields.append('ultima_atividade')
        if final and not job.finalizado_em:
            job.finalizado_em = lote.data_finalizacao or timezone.now()
            fields.append('finalizado_em')
        elif not final and job.finalizado_em:
            job.finalizado_em = None
            fields.append('finalizado_em')
        if new_status != SicarColetaAutomatica.Status.FALHOU and job.erro:
            job.erro = ''
            fields.append('erro')
        if new_status == SicarColetaAutomatica.Status.FALHOU and not job.erro:
            failures = list(lote.itens.exclude(motivo='').values_list('motivo', flat=True)[:3])
            job.erro = ' '.join(failures)[:8000] or lote.motivo_falha
            fields.append('erro')

        # Quando a coleta assistida capturou a data exibida pelo portal, ela só
        # passa a ser considerada "confirmada" depois que o lote concluiu ou
        # comprovou que o conteúdo era idêntico. Assim o próximo assistente pode
        # pular o download sem confundir arquivo baixado com dado efetivamente validado.
        portal_remote_update_date = str((job.detalhes or {}).get('portal_remote_update_date') or '').strip()
        if final and portal_remote_update_date and new_status in {
            SicarColetaAutomatica.Status.CONCLUIDO,
            SicarColetaAutomatica.Status.SEM_ALTERACAO,
        }:
            try:
                record_confirmed_version(
                    uf=job.uf,
                    dataset_slug=job.dataset_slug,
                    remote_update_date=portal_remote_update_date,
                    source_url=str((job.detalhes or {}).get('portal_download_url') or ''),
                    job_id=job.pk,
                    lote_id=job.lote_id,
                    result_status=new_status,
                )
            except Exception:
                logger.warning('Não foi possível registrar a versão confirmada do portal SICAR para %s.', job.dataset_slug, exc_info=True)

        if fields:
            job.save(update_fields=list(dict.fromkeys(fields)))
            updated += 1
    return updated
