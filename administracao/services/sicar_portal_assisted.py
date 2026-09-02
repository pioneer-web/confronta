import json
import os
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

PORTAL_VERSIONS_FILE = '.portal_versions.json'


def sidecar_path(snapshot_path):
    path = Path(snapshot_path)
    return path.with_name(path.name + '.meta.json')


def read_snapshot_metadata(snapshot_path):
    meta = sidecar_path(snapshot_path)
    if not meta.is_file():
        return {}
    try:
        payload = json.loads(meta.read_text(encoding='utf-8'))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _versions_path():
    root = Path(settings.SICAR_AUTO_INBOX)
    root.mkdir(parents=True, exist_ok=True)
    return root / PORTAL_VERSIONS_FILE


def read_confirmed_versions():
    path = _versions_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def record_confirmed_version(*, uf, dataset_slug, remote_update_date, source_url='', job_id=None, lote_id=None, result_status=''):
    remote_update_date = str(remote_update_date or '').strip()
    if not remote_update_date:
        return None
    path = _versions_path()
    payload = read_confirmed_versions()
    payload[dataset_slug] = {
        'uf': str(uf or '').upper(),
        'remote_update_date': remote_update_date,
        'source_url': str(source_url or ''),
        'confirmed_at': timezone.now().isoformat(),
        'job_id': job_id,
        'lote_id': lote_id,
        'result_status': str(result_status or ''),
    }
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)
    return path


def archive_sidecar(snapshot_path, archive_dir, archived_snapshot_name):
    meta = sidecar_path(snapshot_path)
    if not meta.is_file():
        return None
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / (Path(archived_snapshot_name).name + '.meta.json')
    if destination.exists():
        stamp = datetime.now().strftime('%H%M%S')
        destination = archive_dir / f'{Path(archived_snapshot_name).name}.{stamp}.meta.json'
    os.replace(meta, destination)
    return destination
