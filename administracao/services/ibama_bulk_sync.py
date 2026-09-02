from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import tempfile
import time
import unicodedata
import zipfile
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.files import File
from django.db import connection, transaction
from django.utils import timezone
from psycopg import sql

from administracao.models import FonteSincronizacao, User
from administracao.services.confronta_contract import ensure_confronta_analysis_contract
from administracao.services.pipeline import process_import

logger = logging.getLogger(__name__)

IBAMA_DATASET = 'ibama-termos-embargo'
IBAMA_COLLECTOR_VERSION = 'bulk-dados-abertos-v0.4.2'


@dataclass(frozen=True)
class IbamaResourceSpec:
    key: str
    label: str
    ckan_names: tuple[str, ...]
    direct_urls: tuple[str, ...]
    required_alias_groups: tuple[tuple[str, ...], ...]
    required: bool = False
    output_name: str = ''
    table: str = ''


IBAMA_RESOURCE_SPECS = (
    IbamaResourceSpec(
        key='termos',
        label='Termos de Embargo',
        ckan_names=('Termos de embargo',),
        direct_urls=(
            'https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_embargo/termo_embargo/termo_embargo_csv.zip',
            'https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_embargo/termo_embargo/termo_embargo.csv',
        ),
        required_alias_groups=(('SEQ_TAD',), ('GEOM_AREA_EMBARGADA', 'WKT')),
        required=True,
        output_name='termos.bin',
    ),
    IbamaResourceSpec(
        key='itens',
        label='Itens dos termos',
        ckan_names=('Termos de embargo - itens',),
        direct_urls=('https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_embargo/itens/itens.csv',),
        required_alias_groups=(('SEQ_TAD',),),
        output_name='itens.csv',
        table='embargo_item',
    ),
    IbamaResourceSpec(
        key='coordenadas',
        label='Coordenadas geográficas',
        ckan_names=('Termos de embargo - coordenadas geográficas',),
        direct_urls=('https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_embargo/coordenadas/coordenadas.csv',),
        required_alias_groups=(('SEQ_TAD',), ('LONGITUDE', 'NUM_LONGITUDE_TAD'), ('LATITUDE', 'NUM_LATITUDE_TAD')),
        output_name='coordenadas.csv',
        table='embargo_coordenada',
    ),
    IbamaResourceSpec(
        key='decisoes',
        label='Decisões judiciais',
        ckan_names=('Termos de embargo - decisões judiciais',),
        direct_urls=('https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_embargo/decisao/decisao.csv',),
        required_alias_groups=(('SEQ_TAD',), ('SEQ_DECISAO_JUDICIAL',)),
        output_name='decisoes.csv',
        table='embargo_decisao_judicial',
    ),
    IbamaResourceSpec(
        key='enquadramento',
        label='Enquadramento',
        ckan_names=('Termos de embargo - enquadramento',),
        direct_urls=('https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_embargo/enquadramento/enquadramento.csv',),
        required_alias_groups=(('SEQ_TAD',),),
        output_name='enquadramento.csv',
        table='embargo_enquadramento',
    ),
    IbamaResourceSpec(
        key='enquadramento_complementar',
        label='Enquadramento complementar',
        ckan_names=('Termos de embargo - enquadramento complementar',),
        direct_urls=('https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_embargo/enquadramento_complementar/enquadramento_complementar.csv',),
        required_alias_groups=(('SEQ_TAD',),),
        output_name='enquadramento_complementar.csv',
        table='embargo_enquadramento_complementar',
    ),
    IbamaResourceSpec(
        key='historico',
        label='Histórico',
        ckan_names=('Termos de embargo - histórico',),
        direct_urls=('https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_embargo/termo_embargo_historico/termo_embargo_historico.csv',),
        required_alias_groups=(('SEQ_TAD',), ('SEQ_HIST_TAD',)),
        output_name='historico.csv',
        table='embargo_historico',
    ),
)


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


def _system_user():
    email = (getattr(settings, 'SOURCE_AUTOMATION_USER_EMAIL', '') or '').strip().lower()
    if email:
        user = User.objects.filter(email=email, is_active=True).first()
        if user:
            return user
    return User.objects.filter(is_superuser=True, is_active=True).order_by('id').first()


def _request_with_retry(url, *, method='GET', timeout=180, accept='*/*'):
    retries = max(1, int(getattr(settings, 'SOURCE_HTTP_RETRIES', 3)))
    backoff = max(0.2, float(getattr(settings, 'SOURCE_HTTP_RETRY_BACKOFF_SECONDS', 2)))
    last_exc = None
    for attempt in range(1, retries + 1):
        req = Request(
            url,
            method=method,
            headers={
                'User-Agent': 'CONFRONTA-Manage/0.4',
                'Accept': accept,
                'Cache-Control': 'no-cache',
            },
        )
        try:
            return urlopen(req, timeout=timeout)
        except HTTPError as exc:
            if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                raise
            last_exc = exc
        except (URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
        if attempt < retries:
            time.sleep(backoff * attempt)
    raise RuntimeError(f'Falha HTTP após {retries} tentativa(s): {url} — {last_exc}') from last_exc


def _head_signature(url):
    try:
        with _request_with_retry(url, method='HEAD', timeout=90) as response:
            length = response.headers.get('Content-Length') or ''
            modified = response.headers.get('Last-Modified') or ''
            etag = response.headers.get('ETag') or ''
            meta = {
                'url': url,
                'content_length': int(length) if str(length).isdigit() else None,
                'last_modified': modified,
                'etag': etag,
            }
            if not etag and not modified:
                return '', meta
            raw = f'{url}|{length}|{modified}|{etag}'
            return hashlib.sha256(raw.encode('utf-8')).hexdigest(), meta
    except Exception as exc:
        logger.info('IBAMA HEAD indisponível para %s: %s', url, exc)
        return '', {'url': url, 'head_error': str(exc)}


def _download(url: str, target: Path, job: FonteSincronizacao, start_progress: int, end_progress: int, *, base_bytes: int = 0):
    target.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    downloaded = 0
    with _request_with_retry(
        url,
        method='GET',
        timeout=int(getattr(settings, 'SOURCE_DOWNLOAD_TIMEOUT_SECONDS', 7200)),
    ) as response, target.open('wb') as dst:
        total_raw = response.headers.get('Content-Length') or ''
        total = int(total_raw) if total_raw.isdigit() else 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
            sha.update(chunk)
            downloaded += len(chunk)
            job.bytes_baixados = base_bytes + downloaded
            if total:
                ratio = min(1.0, downloaded / total)
                job.progresso = int(start_progress + ratio * (end_progress - start_progress))
            job.ultima_atividade = timezone.now()
            job.save(update_fields=['bytes_baixados', 'progresso', 'ultima_atividade'])
    if downloaded <= 0:
        raise RuntimeError(f'O IBAMA retornou arquivo vazio: {url}')
    return sha.hexdigest(), downloaded


def _latest_success(job):
    return FonteSincronizacao.objects.filter(
        fonte_slug=job.fonte_slug,
        dataset_slug=job.dataset_slug,
        uf=job.uf,
        status__in=[FonteSincronizacao.Status.CONCLUIDO, FonteSincronizacao.Status.SEM_ALTERACAO],
    ).exclude(pk=job.pk).order_by('-finalizado_em', '-pk').first()


def _operational_has_rows():
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('dados_ibama.ibama_embargo')")
        if cursor.fetchone()[0] is None:
            return False
        cursor.execute('SELECT 1 FROM dados_ibama.ibama_embargo LIMIT 1')
        return cursor.fetchone() is not None


def _finish_no_change(job, signature, details=None):
    job.status = FonteSincronizacao.Status.SEM_ALTERACAO
    job.progresso = 100
    job.etapa = 'Sem alteração — banco preservado'
    job.assinatura_remota = signature or job.assinatura_remota
    job.finalizado_em = timezone.now()
    job.ultima_atividade = job.finalizado_em
    if details:
        merged = dict(job.detalhes or {})
        merged.update(details)
        job.detalhes = merged
    job.save()


def _normalize_name(value):
    text = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def _find_header(headers, *aliases):
    lookup = {_normalize_name(item): item for item in headers}
    for alias in aliases:
        found = lookup.get(_normalize_name(alias))
        if found:
            return found
    return None


def _normalize_seq_tad(value):
    """Normaliza a chave oficial sem inventar identificadores.

    O CSV pode expor valores numéricos como ``123.0`` por conversões intermediárias.
    Valores vazios, NaN textuais ou não inteiros são considerados sem chave e devem
    ser quarentenados, nunca publicados silenciosamente.
    """
    text = str(value or '').strip().replace('\ufeff', '')
    if not text or text.lower() in {'nan', 'none', 'null', '<na>'}:
        return ''
    if re.fullmatch(r'[+-]?\d+', text):
        try:
            return str(int(text))
        except ValueError:
            return ''
    if re.fullmatch(r'[+-]?\d+\.0+', text):
        try:
            return str(int(float(text)))
        except ValueError:
            return ''
    return ''


def _ibama_quarantine_dir(job):
    root = Path(getattr(settings, 'QUARANTINE_DIR', tempfile.gettempdir())) / 'ibama_dados_abertos' / f'sync_{job.pk}'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _dedupe_urls(urls):
    result = []
    seen = set()
    for value in urls:
        url = str(value or '').strip()
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result


def _fetch_ckan_resources():
    """Obtém URLs atuais do catálogo CKAN oficial.

    O IBAMA migra arquivos entre servidores. O catálogo pode apontar para uma URL
    diferente da rota histórica; por isso o coletor usa o CKAN apenas como uma
    fonte de descoberta e mantém rotas oficiais conhecidas como fallback.
    """
    api_url = str(getattr(
        settings,
        'IBAMA_CKAN_PACKAGE_URL',
        'https://dadosabertos.ibama.gov.br/api/3/action/package_show?id=fiscalizacao-termo-de-embargo',
    ) or '').strip()
    if not api_url:
        return {}, {'ok': False, 'error': 'CKAN desabilitado'}
    try:
        with _request_with_retry(api_url, timeout=120, accept='application/json') as response:
            raw = response.read()
        payload = json.loads(raw.decode('utf-8', errors='replace'))
        if not payload.get('success') or not isinstance(payload.get('result'), dict):
            raise RuntimeError('Resposta CKAN sem result válido.')
        resources = payload['result'].get('resources') or []
        discovered = {}
        for spec in IBAMA_RESOURCE_SPECS:
            aliases = {_normalize_name(name) for name in spec.ckan_names}
            matches = []
            for resource in resources:
                name = _normalize_name(resource.get('name') or resource.get('description') or '')
                fmt = _normalize_name(resource.get('format') or '')
                url = str(resource.get('url') or '').strip()
                if not url or name not in aliases:
                    continue
                # Prioriza CSV/ZIP. Recursos JSON/XML homônimos não são usados aqui.
                if fmt and fmt not in {'csv', 'zip', 'shp_zip'} and not url.lower().endswith(('.csv', '.zip')):
                    continue
                matches.append({
                    'url': url,
                    'id': resource.get('id'),
                    'name': resource.get('name'),
                    'format': resource.get('format'),
                    'last_modified': resource.get('last_modified'),
                    'created': resource.get('created'),
                    'hash': resource.get('hash'),
                    'size': resource.get('size'),
                })
            if matches:
                discovered[spec.key] = matches
        return discovered, {
            'ok': True,
            'package_id': payload['result'].get('id'),
            'metadata_modified': payload['result'].get('metadata_modified'),
            'resource_count': len(resources),
        }
    except Exception as exc:
        logger.warning('Não foi possível consultar o catálogo CKAN do IBAMA: %s', exc)
        return {}, {'ok': False, 'error': str(exc)}


def _resource_candidates(spec: IbamaResourceSpec, discovered):
    override_name = {
        'termos': 'IBAMA_TERMO_EMBARGO_URL',
        'itens': 'IBAMA_ITENS_URL',
        'coordenadas': 'IBAMA_COORDENADAS_URL',
        'decisoes': 'IBAMA_DECISOES_URL',
        'enquadramento': 'IBAMA_ENQUADRAMENTO_URL',
        'enquadramento_complementar': 'IBAMA_ENQUADRAMENTO_COMPLEMENTAR_URL',
        'historico': 'IBAMA_HISTORICO_URL',
    }.get(spec.key)
    values = []
    if override_name:
        override = str(getattr(settings, override_name, '') or '').strip()
        if override:
            values.append(override)
    values.extend(spec.direct_urls)
    for item in discovered.get(spec.key, []):
        values.append(item.get('url'))
    return _dedupe_urls(values)


def _looks_like_html(path: Path):
    raw = path.read_bytes()[:4096].lstrip().lower()
    return raw.startswith(b'<!doctype html') or raw.startswith(b'<html') or b'<html' in raw[:512]


def _materialize_main_csv(source_path: Path, target_dir: Path):
    """Aceita tanto o ZIP oficial quanto CSV direto.

    O portal já publicou o mesmo recurso em rotas/formatos diferentes. O importador
    não deve quebrar apenas porque a URL passou de ZIP para CSV.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    if _looks_like_html(source_path):
        raise RuntimeError('O recurso principal do IBAMA retornou HTML em vez de dados.')
    if zipfile.is_zipfile(source_path):
        with zipfile.ZipFile(source_path) as zf:
            members = [m for m in zf.infolist() if not m.is_dir() and m.filename.lower().endswith('.csv')]
            if not members:
                raise RuntimeError('O ZIP oficial do IBAMA não contém arquivo CSV.')
            members.sort(key=lambda m: ('termo_embargo' not in _normalize_name(Path(m.filename).stem), len(m.filename)))
            member = members[0]
            max_uncompressed = int(getattr(settings, 'IBAMA_MAX_MAIN_CSV_BYTES', 2147483648))
            if int(member.file_size or 0) > max_uncompressed:
                raise RuntimeError('O CSV principal do IBAMA excede o limite defensivo configurado.')
            if member.compress_size and member.file_size / max(1, member.compress_size) > 500:
                raise RuntimeError('O ZIP principal do IBAMA apresentou taxa de compressão anormal.')
            resolved = (target_dir / Path(member.filename).name).resolve()
            if target_dir.resolve() not in resolved.parents:
                raise RuntimeError('Nome de arquivo inseguro no ZIP do IBAMA.')
            with zf.open(member) as src, resolved.open('wb') as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
    else:
        resolved = (target_dir / 'termo_embargo.csv').resolve()
        shutil.copy2(source_path, resolved)
    if resolved.stat().st_size <= 0:
        raise RuntimeError('O CSV principal do IBAMA está vazio.')
    # Validação leve de conteúdo antes da análise completa.
    encoding, sep = _csv_encoding_and_separator(resolved)
    with resolved.open('r', encoding=encoding, errors='replace', newline='') as fh:
        header = next(csv.reader(fh, delimiter=sep), None) or []
    if not _find_header(header, 'SEQ_TAD'):
        raise RuntimeError('O recurso principal do IBAMA não contém SEQ_TAD; pode ser página de erro ou schema incompatível.')
    return resolved


def _download_resource(spec: IbamaResourceSpec, candidates, target: Path, job, start_progress, end_progress, *, base_bytes=0):
    attempts = []
    last_error = None
    for idx, url in enumerate(candidates, start=1):
        temp = target.with_name(f'{target.name}.part{idx}')
        try:
            if temp.exists():
                temp.unlink()
            content_hash, size = _download(
                url, temp, job, start_progress, end_progress, base_bytes=base_bytes,
            )
            if _looks_like_html(temp):
                raise RuntimeError('servidor devolveu HTML em vez do arquivo esperado')
            if spec.key == 'termos':
                # Materialização temporária valida ZIP ou CSV e o cabeçalho SEQ_TAD.
                with tempfile.TemporaryDirectory(prefix='ibama_probe_') as probe:
                    probe_csv = _materialize_main_csv(temp, Path(probe))
                    _validate_aux_csv(probe_csv, spec.label, spec.required_alias_groups)
            else:
                _validate_aux_csv(temp, spec.label, spec.required_alias_groups)
            temp.replace(target)
            attempts.append({'url': url, 'ok': True, 'bytes': size})
            return {
                'path': target, 'url': url, 'sha256': content_hash, 'bytes': size, 'attempts': attempts,
            }
        except Exception as exc:
            last_error = exc
            attempts.append({'url': url, 'ok': False, 'error': str(exc)})
            logger.warning('IBAMA %s: falha em %s: %s', spec.key, url, exc)
            try:
                temp.unlink(missing_ok=True)
            except Exception:
                pass
    if spec.required:
        attempted = '; '.join(item['url'] for item in attempts) or '(nenhuma URL)'
        raise RuntimeError(
            f'Não foi possível obter o recurso obrigatório “{spec.label}” do IBAMA. '
            f'Rotas oficiais tentadas: {attempted}. Último erro: {last_error}'
        )
    return {'path': None, 'url': '', 'sha256': '', 'bytes': 0, 'attempts': attempts, 'error': str(last_error or 'indisponível')}


def _csv_encoding_and_separator(path: Path):
    raw = path.read_bytes()[:65536]
    encodings = ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1')
    decoded = None
    encoding = None
    for candidate in encodings:
        try:
            decoded = raw.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        encoding = 'latin-1'
        decoded = raw.decode(encoding, errors='replace')
    first_line = decoded.splitlines()[0] if decoded.splitlines() else ''
    counts = {sep: first_line.count(sep) for sep in (';', ',', '\t', '|')}
    separator = max(counts, key=counts.get)
    if counts[separator] == 0:
        separator = ';'
    return encoding, separator


def _polygonalize(geom):
    if geom is None:
        return None
    try:
        from shapely import force_2d, get_parts, make_valid
        from shapely.geometry import MultiPolygon, Polygon
    except Exception:
        return geom
    try:
        geom = force_2d(geom)
        if geom.is_empty:
            return None
        if not geom.is_valid:
            geom = make_valid(geom)
        if isinstance(geom, Polygon):
            return MultiPolygon([geom])
        if isinstance(geom, MultiPolygon):
            return geom
        polygons = []
        for part in get_parts(geom):
            if isinstance(part, Polygon):
                polygons.append(part)
            elif isinstance(part, MultiPolygon):
                polygons.extend(list(part.geoms))
        if not polygons:
            return None
        return MultiPolygon(polygons)
    except Exception:
        return None


def _validate_aux_csv(path: Path, label: str, required_alias_groups):
    import pandas as pd
    encoding, sep = _csv_encoding_and_separator(path)
    try:
        header = list(pd.read_csv(path, sep=sep, encoding=encoding, nrows=0).columns)
    except Exception as exc:
        raise RuntimeError(f'O recurso {label} do IBAMA não é um CSV legível: {exc}') from exc
    if not header:
        raise RuntimeError(f'O recurso {label} do IBAMA está sem cabeçalho.')
    normalized = {_normalize_name(c) for c in header}
    for aliases in required_alias_groups:
        if not any(_normalize_name(alias) in normalized for alias in aliases):
            expected = '/'.join(aliases)
            raise RuntimeError(f'O recurso {label} do IBAMA não contém o campo esperado {expected}.')
    return {'encoding': encoding, 'separator': sep, 'header': header}


def _analyze_main_csv(csv_path: Path):
    import pandas as pd
    from shapely import from_wkt, is_empty, is_missing

    encoding, sep = _csv_encoding_and_separator(csv_path)
    header = list(pd.read_csv(csv_path, sep=sep, encoding=encoding, nrows=0).columns)
    seq_col = _find_header(header, 'SEQ_TAD')
    geom_col = _find_header(header, 'GEOM_AREA_EMBARGADA', 'WKT')
    if not seq_col:
        raise RuntimeError('O arquivo principal do IBAMA não contém SEQ_TAD. Base anterior preservada.')
    if not geom_col:
        raise RuntimeError('O arquivo principal do IBAMA não contém WKT/GEOM_AREA_EMBARGADA. Base anterior preservada.')

    chunk_size = max(500, int(getattr(settings, 'IBAMA_CSV_CHUNK_SIZE', 5000)))
    keys = set()
    duplicates = set()
    duplicate_rows = 0
    missing_geometry = set()
    records = 0
    valid_records = 0
    invalid_key_rows = 0
    invalid_key_examples = []
    usecols = [seq_col, geom_col]
    for chunk in pd.read_csv(
        csv_path,
        sep=sep,
        encoding=encoding,
        dtype=str,
        keep_default_na=False,
        usecols=usecols,
        chunksize=chunk_size,
        low_memory=False,
    ):
        records += len(chunk)
        normalized_keys = [_normalize_seq_tad(raw) for raw in chunk[seq_col].astype(str)]
        for raw, key in zip(chunk[seq_col].astype(str), normalized_keys):
            if not key:
                invalid_key_rows += 1
                if len(invalid_key_examples) < 5:
                    invalid_key_examples.append(str(raw).strip())
                continue
            valid_records += 1
            if key in keys:
                duplicates.add(key)
                duplicate_rows += 1
            keys.add(key)

        wkts = chunk[geom_col].astype(str).str.strip()
        parsed = from_wkt(wkts.to_numpy(), on_invalid='ignore')
        for idx, geom in enumerate(parsed):
            key = normalized_keys[idx]
            if not key:
                continue
            if not wkts.iloc[idx] or bool(is_missing(geom)) or bool(is_empty(geom)) or _polygonalize(geom) is None:
                missing_geometry.add(key)

    if records <= 0:
        raise RuntimeError('O arquivo principal do IBAMA não possui registros.')
    if valid_records <= 0:
        raise RuntimeError('O arquivo principal do IBAMA não possui nenhum SEQ_TAD válido. Base anterior preservada.')

    # Falhas isoladas de qualidade são conhecidas em dados abertos e não devem
    # derrubar a carga nacional inteira. Uma proporção alta, contudo, sinaliza
    # provável mudança de schema/delimitador e bloqueia a publicação.
    invalid_ratio = invalid_key_rows / max(1, records)
    max_invalid_ratio = float(getattr(settings, 'IBAMA_MAX_INVALID_KEY_RATIO', 0.02))
    if invalid_ratio > max_invalid_ratio:
        raise RuntimeError(
            f'O arquivo do IBAMA apresentou {invalid_key_rows} registro(s) sem SEQ_TAD '
            f'({invalid_ratio:.2%}), acima do limite defensivo de {max_invalid_ratio:.2%}. '
            'Possível alteração de schema ou arquivo corrompido; base anterior preservada.'
        )

    return {
        'encoding': encoding,
        'separator': sep,
        'header': header,
        'seq_col': seq_col,
        'geom_col': geom_col,
        'keys': keys,
        'missing_geometry': missing_geometry,
        'records': records,
        'valid_records': valid_records,
        'invalid_key_rows': invalid_key_rows,
        'invalid_key_examples': invalid_key_examples,
        'duplicate_keys': duplicates,
        'duplicate_rows': duplicate_rows,
        'invalid_key_ratio': invalid_ratio,
    }


def _coordinate_fallback(csv_path: Path, wanted_keys: set[str]):
    if not wanted_keys or not csv_path.exists():
        return {}
    import pandas as pd
    from shapely.geometry import MultiPolygon, Polygon

    encoding, sep = _csv_encoding_and_separator(csv_path)
    header = list(pd.read_csv(csv_path, sep=sep, encoding=encoding, nrows=0).columns)
    seq_col = _find_header(header, 'SEQ_TAD')
    lon_col = _find_header(header, 'LONGITUDE', 'NUM_LONGITUDE_TAD')
    lat_col = _find_header(header, 'LATITUDE', 'NUM_LATITUDE_TAD')
    if not seq_col or not lon_col or not lat_col:
        return {}
    polygon_cols = [
        c for c in (
            _find_header(header, 'SEQ_POLIGONO'),
            _find_header(header, 'SQ_POLIGONO_AIEMOB'),
            _find_header(header, 'NO_AREA'),
        ) if c
    ]
    order_col = _find_header(header, 'ORDEM', 'SEQ_PONTO_POLIGONO', 'SQ_COORDENADA_AIEMOB')
    usecols = [seq_col, lon_col, lat_col] + polygon_cols + ([order_col] if order_col else [])
    groups = defaultdict(lambda: defaultdict(list))
    chunk_size = max(1000, int(getattr(settings, 'IBAMA_CSV_CHUNK_SIZE', 5000)))
    row_counter = 0
    for chunk in pd.read_csv(
        csv_path,
        sep=sep,
        encoding=encoding,
        dtype=str,
        keep_default_na=False,
        usecols=list(dict.fromkeys(usecols)),
        chunksize=chunk_size,
        low_memory=False,
    ):
        for _, row in chunk.iterrows():
            row_counter += 1
            key = _normalize_seq_tad(row.get(seq_col, ''))
            if not key or key not in wanted_keys:
                continue
            try:
                lon = float(str(row.get(lon_col, '')).replace(',', '.'))
                lat = float(str(row.get(lat_col, '')).replace(',', '.'))
            except (TypeError, ValueError):
                continue
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                continue
            polygon_id = '|'.join(str(row.get(c, '')).strip() for c in polygon_cols).strip('|') or 'default'
            order_raw = str(row.get(order_col, '')).strip() if order_col else ''
            try:
                order_value = float(order_raw.replace(',', '.'))
            except (TypeError, ValueError):
                order_value = float(row_counter)
            groups[key][polygon_id].append((order_value, lon, lat))

    result = {}
    for key, polygon_groups in groups.items():
        polygons = []
        for points in polygon_groups.values():
            points.sort(key=lambda item: item[0])
            coords = [(lon, lat) for _, lon, lat in points]
            if len(coords) < 3:
                continue
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            try:
                geom = _polygonalize(Polygon(coords))
                if geom is None:
                    continue
                if isinstance(geom, MultiPolygon):
                    polygons.extend(list(geom.geoms))
                else:
                    polygons.append(geom)
            except Exception:
                continue
        if polygons:
            result[key] = MultiPolygon(polygons)
    return result


def _extract_wkt_from_payload(payload):
    if payload is None:
        return ''
    if isinstance(payload, str):
        text = payload.strip()
        match = re.search(r'\b(?:MULTIPOLYGON|POLYGON|GEOMETRYCOLLECTION)\s*(?:ZM|Z|M)?\s*\(.+', text, re.I | re.S)
        return match.group(0).strip('" \n\r\t') if match else ''
    if isinstance(payload, dict):
        preferred = ('wkt', 'geom', 'geometry', 'geometria', 'geom_area_embargada', 'resultado', 'result', 'data')
        for key in preferred:
            if key in payload:
                found = _extract_wkt_from_payload(payload[key])
                if found:
                    return found
        for value in payload.values():
            found = _extract_wkt_from_payload(value)
            if found:
                return found
    if isinstance(payload, (list, tuple)):
        for value in payload:
            found = _extract_wkt_from_payload(value)
            if found:
                return found
    return ''


def _api_wkt_fallback(wanted_keys: set[str], job: FonteSincronizacao):
    if not wanted_keys or not getattr(settings, 'IBAMA_WKT_API_ENABLED', True):
        return {}, 0
    from shapely import from_wkt

    template = str(getattr(settings, 'IBAMA_WKT_API_URL_TEMPLATE', '') or '').strip()
    if not template:
        return {}, 0
    limit = max(0, int(getattr(settings, 'IBAMA_WKT_API_MAX_REQUESTS', 5000)))
    if limit == 0:
        return {}, 0
    result = {}
    attempted = 0
    total = min(len(wanted_keys), limit)
    for idx, key in enumerate(sorted(wanted_keys)[:limit], start=1):
        attempted += 1
        url = template.format(seq_tad=quote(str(key), safe=''))
        try:
            with _request_with_retry(url, timeout=90, accept='application/json,text/plain,*/*') as response:
                raw = response.read().decode('utf-8', errors='replace').strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            wkt_text = _extract_wkt_from_payload(payload)
            if not wkt_text:
                continue
            geom = _polygonalize(from_wkt(wkt_text, on_invalid='ignore'))
            if geom is not None:
                result[str(key)] = geom
        except Exception as exc:
            logger.info('API WKT IBAMA não retornou geometria para SEQ_TAD=%s: %s', key, exc)
        if idx == 1 or idx % 50 == 0 or idx == total:
            _touch(
                job,
                progress=min(62, 58 + int((idx / max(1, total)) * 4)),
                stage=f'Complementando geometrias IBAMA — {idx} de {total}',
            )
    return result, attempted


def _build_gpkg(main_csv: Path, analysis: dict, fallback_geometries: dict[str, object], target: Path, job):
    import geopandas as gpd
    import pandas as pd
    import pyogrio
    from shapely import from_wkt

    if target.exists():
        target.unlink()
    encoding = analysis['encoding']
    sep = analysis['separator']
    seq_col = analysis['seq_col']
    geom_col = analysis['geom_col']
    source_total = int(analysis['records'])
    expected_valid = int(analysis['valid_records'])
    chunk_size = max(500, int(getattr(settings, 'IBAMA_CSV_CHUNK_SIZE', 5000)))
    written = 0
    processed = 0
    first = True
    missing_after = 0
    invalid_wkt = 0
    rejected_missing_key = 0
    quarantine_path = _ibama_quarantine_dir(job) / 'termos_rejeitados_sem_seq_tad.csv'
    quarantine_header_written = quarantine_path.exists() and quarantine_path.stat().st_size > 0

    for chunk in pd.read_csv(
        main_csv,
        sep=sep,
        encoding=encoding,
        dtype=str,
        keep_default_na=False,
        chunksize=chunk_size,
        low_memory=False,
    ):
        processed += len(chunk)
        normalized_keys = chunk[seq_col].map(_normalize_seq_tad)
        invalid_mask = normalized_keys.eq('')
        if invalid_mask.any():
            rejected = chunk.loc[invalid_mask].copy()
            rejected.insert(0, '_motivo_confronta', 'SEQ_TAD ausente ou inválido na fonte oficial')
            rejected.to_csv(
                quarantine_path,
                sep=';',
                index=False,
                mode='a',
                header=not quarantine_header_written,
                encoding='utf-8',
            )
            quarantine_header_written = True
            rejected_missing_key += int(invalid_mask.sum())

        chunk = chunk.loc[~invalid_mask].copy()
        if chunk.empty:
            continue
        chunk[seq_col] = normalized_keys.loc[~invalid_mask].values

        wkts = chunk[geom_col].astype(str).str.strip()
        parsed = from_wkt(wkts.to_numpy(), on_invalid='ignore')
        geometries = []
        for idx, geom in enumerate(parsed):
            key = str(chunk.iloc[idx][seq_col]).strip()
            normalized = _polygonalize(geom) if wkts.iloc[idx] else None
            if normalized is None and wkts.iloc[idx]:
                invalid_wkt += 1
            if normalized is None:
                normalized = fallback_geometries.get(key)
            if normalized is None:
                missing_after += 1
            geometries.append(normalized)

        # O WKT original permanece como atributo RAW; geometry é a versão espacial normalizada.
        gdf = gpd.GeoDataFrame(chunk, geometry=geometries, crs='EPSG:4674')
        pyogrio.write_dataframe(
            gdf,
            target,
            layer='termo_embargo',
            driver='GPKG',
            append=not first,
        )
        first = False
        written += len(gdf)
        progress = 63 + int((processed / max(1, source_total)) * 17)
        _touch(
            job,
            status=FonteSincronizacao.Status.VALIDANDO,
            progress=min(80, progress),
            stage=f'Preparando base espacial IBAMA — {processed:,} de {source_total:,}'.replace(',', '.'),
        )

    if first or written != expected_valid:
        raise RuntimeError(
            f'GeoPackage IBAMA incompleto: {written} registro(s) válidos gravados de {expected_valid} esperados. '
            'Base anterior preservada.'
        )
    info = pyogrio.read_info(target, layer='termo_embargo')
    if int(info.get('features') or 0) != expected_valid:
        raise RuntimeError('A validação final do GeoPackage IBAMA divergiu dos registros válidos do CSV oficial.')
    return {
        'registros_fonte': source_total,
        'registros_validos': expected_valid,
        'registros_rejeitados_sem_seq_tad': rejected_missing_key,
        'arquivo_quarentena': str(quarantine_path) if rejected_missing_key else '',
        'chaves_duplicadas_fonte': len(analysis.get('duplicate_keys') or ()),
        'linhas_duplicadas_fonte': int(analysis.get('duplicate_rows') or 0),
        'geometrias_ausentes': missing_after,
        'wkt_invalidos': invalid_wkt,
        'bytes_gpkg': target.stat().st_size,
    }


def _normalize_sql_name(value):
    text = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii')
    text = re.sub(r'[^a-zA-Z0-9_]+', '_', text.strip().lower()).strip('_')
    if not text:
        text = 'campo'
    if text[0].isdigit():
        text = f'c_{text}'
    return text[:60]


def _import_csv_snapshot(path: Path, table: str, source_url: str):
    """Substitui tabela complementar por snapshot, sem publicar linhas sem SEQ_TAD.

    Complementos sem chave não conseguem ser relacionados ao termo principal; eles são
    contabilizados como rejeitados em vez de derrubar a atualização nacional.
    """
    encoding, sep = _csv_encoding_and_separator(path)
    with path.open('r', encoding=encoding, errors='replace', newline='') as fh:
        reader = csv.reader(fh, delimiter=sep)
        header = next(reader, None)
        if not header:
            return {'registros': 0, 'rejeitados_sem_seq_tad': 0}
        columns = []
        seen = set()
        for idx, raw in enumerate(header):
            name = _normalize_sql_name(raw) or f'campo_{idx+1}'
            base = name
            suffix = 2
            while name in seen:
                name = f'{base}_{suffix}'
                suffix += 1
            seen.add(name)
            columns.append(name)
        seq_idx = columns.index('seq_tad') if 'seq_tad' in columns else None

        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute('CREATE SCHEMA IF NOT EXISTS dados_ibama')
            temp = f'{table}__novo'
            cursor.execute(sql.SQL('DROP TABLE IF EXISTS dados_ibama.{}').format(sql.Identifier(temp)))
            defs = sql.SQL(',').join(sql.SQL('{} text').format(sql.Identifier(c)) for c in columns)
            cursor.execute(
                sql.SQL('CREATE TABLE dados_ibama.{} ({}, fonte_url text, data_importacao timestamptz DEFAULT CURRENT_TIMESTAMP)').format(
                    sql.Identifier(temp), defs
                )
            )
            copy_stmt = sql.SQL('COPY dados_ibama.{} ({}) FROM STDIN').format(
                sql.Identifier(temp),
                sql.SQL(',').join(sql.Identifier(c) for c in columns + ['fonte_url']),
            )
            count = 0
            rejected = 0
            with cursor.copy(copy_stmt) as copy:
                for row in reader:
                    row = list(row[:len(columns)]) + [''] * max(0, len(columns) - len(row))
                    if seq_idx is not None:
                        normalized = _normalize_seq_tad(row[seq_idx])
                        if not normalized:
                            rejected += 1
                            continue
                        row[seq_idx] = normalized
                    copy.write_row(row + [source_url])
                    count += 1
            cursor.execute(sql.SQL('DROP TABLE IF EXISTS dados_ibama.{}').format(sql.Identifier(table)))
            cursor.execute(sql.SQL('ALTER TABLE dados_ibama.{} RENAME TO {}').format(
                sql.Identifier(temp), sql.Identifier(table)
            ))
            if 'seq_tad' in columns:
                cursor.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON dados_ibama.{} (seq_tad)').format(
                    sql.Identifier(f'idx_{table[:40]}_seq_tad'), sql.Identifier(table)
                ))
    return {'registros': count, 'rejeitados_sem_seq_tad': rejected}


def _db_fingerprint_map():
    columns = (
        'numero_embargo', 'serie_embargo', 'data_embargo', 'data_impressao', 'forma_entrega',
        'status_original', 'status_aie', 'sit_cancelado', 'sit_desembargo', 'tipo_desembargo',
        'data_desembargo', 'descricao_desembargo', 'processo', 'seq_auto_infracao', 'auto_infracao',
        'seq_notificacao', 'nome_embargado', 'cpf_cnpj', 'municipio', 'codigo_municipio', 'uf',
        'localizacao', 'nome_imovel', 'tipo_area', 'area_embargada_ha', 'descricao_infracao',
        'latitude', 'longitude', 'deter_prodes', 'id_poligono', 'embarga_poligono',
        'data_geometria', 'data_ultima_alteracao', 'tipo_alteracao', 'justificativa_alteracao',
        'seq_acao_fiscalizatoria', 'codigo_acao_fiscalizatoria', 'operacao',
        'seq_ordem_fiscalizacao', 'ordem_fiscalizacao', 'unid_ordenadora',
        'unidade_apresentacao', 'unidade_controle', 'orgao', 'ultima_atualizacao_relatorio',
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('dados_ibama.ibama_embargo')")
        if cursor.fetchone()[0] is None:
            return {}
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='dados_ibama' AND table_name='ibama_embargo'"
        )
        existing = {r[0] for r in cursor.fetchall()}
        if 'seq_tad' not in existing:
            return {}
        usable = [c for c in columns if c in existing]
        parts = [sql.SQL("COALESCE({}::text,'')").format(sql.Identifier(c)) for c in usable]
        if 'geometry' in existing:
            parts.append(sql.SQL("COALESCE(encode(ST_AsEWKB(geometry),'hex'),'')"))
        fingerprint = sql.SQL("md5(concat_ws(chr(31), {}))").format(sql.SQL(',').join(parts))
        cursor.execute(sql.SQL('SELECT seq_tad::text, {} FROM dados_ibama.ibama_embargo WHERE seq_tad IS NOT NULL').format(fingerprint))
        return {str(key): fp for key, fp in cursor.fetchall()}


def _normalize_operational():
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('dados_ibama.ibama_embargo')")
        if cursor.fetchone()[0] is None:
            return {}
        cursor.execute('ALTER TABLE dados_ibama.ibama_embargo ADD COLUMN IF NOT EXISTS status_normalizado text')
        cursor.execute(
            """
            UPDATE dados_ibama.ibama_embargo
               SET status_normalizado = CASE
                   WHEN upper(coalesce(sit_cancelado,'')) = 'S'
                        OR upper(coalesce(status_original,'')) LIKE '%CANCEL%'
                     THEN 'CANCELADO'
                   WHEN upper(coalesce(sit_desembargo,'')) = 'S'
                        OR upper(coalesce(tipo_desembargo,'')) LIKE '%DESEMBARG%'
                     THEN 'DESEMBARGADO'
                   WHEN upper(coalesce(status_original,'')) LIKE '%SUBSTITU%'
                        OR upper(coalesce(status_original,'')) LIKE '%EXCLU%'
                        OR upper(coalesce(status_aie,'')) LIKE '%EXCLU%'
                     THEN 'A_VERIFICAR'
                   WHEN nullif(trim(coalesce(status_original,'') || ' ' || coalesce(status_aie,'')), '') IS NULL
                     THEN 'A_VERIFICAR'
                   ELSE 'ATIVO'
               END
            """
        )
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ibama_embargo_status_norm ON dados_ibama.ibama_embargo(status_normalizado)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ibama_embargo_uf ON dados_ibama.ibama_embargo(uf)')
        cursor.execute('SELECT status_normalizado, COUNT(*) FROM dados_ibama.ibama_embargo GROUP BY status_normalizado')
        return {str(status or 'A_VERIFICAR'): int(total) for status, total in cursor.fetchall()}


def _process_gpkg(job, gpkg: Path, user):
    _touch(job, status=FonteSincronizacao.Status.VALIDANDO, progress=81, stage='Validando e promovendo base IBAMA')

    def progress(percent, stage):
        mapped = 81 + int(max(0, min(100, percent)) * 0.14)
        _touch(job, status=FonteSincronizacao.Status.IMPORTANDO, progress=min(95, mapped), stage=stage)

    with gpkg.open('rb') as raw:
        uploaded = File(raw, name=gpkg.name)
        imp = process_import(
            uploaded,
            IBAMA_DATASET,
            user,
            context={'sincronizacao_id': job.pk, 'origem_automatica': True, 'fonte_bulk': 'dados_abertos_ibama'},
            progress_callback=progress,
        )
    job.importacao = imp
    job.save(update_fields=['importacao'])
    return imp


def _combined_content_signature(downloads):
    parts = []
    for key in sorted(downloads):
        item = downloads[key]
        # Ausência de complemento também entra na assinatura. Se o recurso voltar
        # a ficar disponível, a assinatura muda e o banco complementar é atualizado.
        parts.append(f"{key}:{item.get('sha256') or 'MISSING'}")
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()


def process_ibama_bulk_job(job: FonteSincronizacao):
    user = job.solicitado_por or _system_user()
    if not user:
        raise RuntimeError('Nenhum administrador ativo foi encontrado para registrar a importação automática.')

    _touch(
        job,
        status=FonteSincronizacao.Status.VERIFICANDO,
        progress=3,
        stage='Descobrindo recursos oficiais do Dados Abertos IBAMA',
        details={
            'estrategia_ibama': IBAMA_COLLECTOR_VERSION,
            'coletor': 'Dados Abertos IBAMA / CSV',
        },
        error='',
    )

    discovered, ckan_meta = _fetch_ckan_resources()
    resource_specs = {spec.key: spec for spec in IBAMA_RESOURCE_SPECS}
    candidates = {key: _resource_candidates(spec, discovered) for key, spec in resource_specs.items()}
    if not candidates.get('termos'):
        raise RuntimeError('Nenhuma rota oficial foi encontrada para o recurso Termos de Embargo do IBAMA.')

    previous = _latest_success(job)
    old_fingerprints = _db_fingerprint_map()

    with tempfile.TemporaryDirectory(prefix='confronta_ibama_bulk_') as tmp:
        tmpdir = Path(tmp)
        downloads = {}
        cumulative_bytes = 0

        # O recurso principal é obrigatório. Os complementares são deliberadamente
        # opcionais: uma indisponibilidade temporária não pode derrubar a base nacional.
        ordered = (
            ('termos', 7, 31),
            ('itens', 32, 38),
            ('coordenadas', 39, 46),
            ('decisoes', 47, 51),
            ('enquadramento', 52, 55),
            ('enquadramento_complementar', 56, 58),
            ('historico', 59, 62),
        )
        for key, p0, p1 in ordered:
            spec = resource_specs[key]
            target = tmpdir / spec.output_name
            _touch(
                job,
                status=FonteSincronizacao.Status.BAIXANDO,
                progress=p0,
                stage=f'Baixando {spec.label}',
                details={'recurso_atual': key},
            )
            item = _download_resource(
                spec,
                candidates.get(key, []),
                target,
                job,
                p0,
                p1,
                base_bytes=cumulative_bytes,
            )
            downloads[key] = item
            cumulative_bytes += int(item.get('bytes') or 0)
            job.bytes_baixados = cumulative_bytes
            job.save(update_fields=['bytes_baixados'])

        content_signature = _combined_content_signature(downloads)
        job.assinatura_remota = content_signature
        job.save(update_fields=['assinatura_remota'])

        # Só encerramos por hash depois de validar que o arquivo principal foi
        # realmente baixado e reconhecido como dado oficial. Isso evita falso
        # “sem alteração” causado por HEAD/redirect/metadado stale.
        if (
            previous
            and previous.assinatura_remota == content_signature
            and _operational_has_rows()
        ):
            _finish_no_change(
                job,
                content_signature,
                {
                    'estrategia_ibama': IBAMA_COLLECTOR_VERSION,
                            'ckan': ckan_meta,
                    'recursos': {
                        key: {
                            'url': item.get('url'),
                            'bytes': item.get('bytes'),
                            'disponivel': bool(item.get('path')),
                            'tentativas': item.get('attempts'),
                        }
                        for key, item in downloads.items()
                    },
                },
            )
            return

        _touch(job, status=FonteSincronizacao.Status.VALIDANDO, progress=63, stage='Validando Termos de Embargo')
        main_csv = _materialize_main_csv(downloads['termos']['path'], tmpdir / 'main')
        analysis = _analyze_main_csv(main_csv)
        job.registros_fonte = int(analysis['records'])
        job.detalhes = {
            **(job.detalhes or {}),
            'qualidade_fonte_principal': {
                'registros_fonte': int(analysis['records']),
                'registros_validos': int(analysis['valid_records']),
                'sem_seq_tad': int(analysis['invalid_key_rows']),
                'chaves_duplicadas': len(analysis['duplicate_keys']),
                'linhas_duplicadas': int(analysis['duplicate_rows']),
            },
        }
        job.save(update_fields=['registros_fonte', 'detalhes'])

        missing = set(analysis['missing_geometry'])
        coord_fallback = {}
        coords_path = downloads['coordenadas'].get('path')
        if missing and coords_path:
            _touch(
                job,
                progress=66,
                stage='Complementando geometrias pelas coordenadas oficiais',
                details={'geometrias_sem_wkt_inicial': len(missing)},
            )
            coord_fallback = _coordinate_fallback(coords_path, missing)

        remaining = missing - set(coord_fallback)
        api_fallback = {}
        api_attempts = 0
        if remaining:
            _touch(job, progress=68, stage='Consultando API WKT somente para geometrias faltantes')
            api_fallback, api_attempts = _api_wkt_fallback(remaining, job)
        fallback_geometries = {**coord_fallback, **api_fallback}

        gpkg = tmpdir / 'IBAMA_Termos_Embargo_Brasil.gpkg'
        gpkg_stats = _build_gpkg(main_csv, analysis, fallback_geometries, gpkg, job)
        _touch(
            job,
            details={
                'geometrias': {
                    'sem_wkt_inicial': len(missing),
                    'reconstruidas_coordenadas': len(coord_fallback),
                    'obtidas_api_wkt': len(api_fallback),
                    'tentativas_api_wkt': api_attempts,
                    'sem_geometria_final': gpkg_stats['geometrias_ausentes'],
                    'wkt_invalidos': gpkg_stats['wkt_invalidos'],
                }
            },
        )

        imp = _process_gpkg(job, gpkg, user)
        allowed = {imp.Status.CONCLUIDO, imp.Status.SEM_ALTERACAO, imp.Status.IGNORADO_DUPLICADO}
        if imp.status not in allowed:
            raise RuntimeError(imp.motivo_rejeicao or f'Importação IBAMA terminou em {imp.get_status_display()}.')

        status_counts = _normalize_operational()
        new_fingerprints = _db_fingerprint_map()
        old_keys = set(old_fingerprints)
        new_keys = set(new_fingerprints)
        common = old_keys & new_keys
        job.novos = len(new_keys - old_keys)
        job.removidos = len(old_keys - new_keys)
        job.alterados = sum(1 for key in common if old_fingerprints[key] != new_fingerprints[key])
        job.save(update_fields=['novos', 'alterados', 'removidos'])

        aux = {}
        for key in ('itens', 'coordenadas', 'decisoes', 'enquadramento', 'enquadramento_complementar', 'historico'):
            spec = resource_specs[key]
            item = downloads[key]
            path = item.get('path')
            if not path:
                aux[key] = {'disponivel': False, 'erro': item.get('error') or 'Recurso indisponível'}
                continue
            try:
                _touch(job, progress=96, stage=f'Atualizando complemento IBAMA — {spec.label}')
                stats = _import_csv_snapshot(path, spec.table, item.get('url') or '')
                aux[key] = {
                    'disponivel': True,
                    'registros': int(stats.get('registros') or 0),
                    'rejeitados_sem_seq_tad': int(stats.get('rejeitados_sem_seq_tad') or 0),
                    'url': item.get('url'),
                }
            except Exception as exc:
                # Complementos enriquecem “Ver detalhes”, mas não podem invalidar
                # a tabela principal já validada/promovida.
                logger.warning('Falha complementar IBAMA %s: %s', key, exc)
                aux[key] = {'disponivel': False, 'erro': str(exc), 'url': item.get('url')}

        ensure_confronta_analysis_contract()

    job.status = (
        FonteSincronizacao.Status.CONCLUIDO
        if imp.status == imp.Status.CONCLUIDO
        else FonteSincronizacao.Status.SEM_ALTERACAO
    )
    job.progresso = 100
    job.etapa = (
        'IBAMA atualizado no PostGIS'
        if job.status == FonteSincronizacao.Status.CONCLUIDO
        else 'Sem alteração — banco preservado'
    )
    job.finalizado_em = timezone.now()
    job.ultima_atividade = job.finalizado_em
    job.detalhes = {
        **(job.detalhes or {}),
        'estrategia_ibama': IBAMA_COLLECTOR_VERSION,
        'ckan': ckan_meta,
        'recursos': {
            key: {
                'url': item.get('url'),
                'bytes': item.get('bytes'),
                'disponivel': bool(item.get('path')),
                'tentativas': item.get('attempts'),
            }
            for key, item in downloads.items()
        },
        'status_normalizados': status_counts,
        'complementos': aux,
        'gpkg': gpkg_stats,
    }
    job.save()

