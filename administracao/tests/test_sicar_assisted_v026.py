import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from administracao.services.sicar_portal_assisted import (
    read_confirmed_versions,
    read_snapshot_metadata,
    record_confirmed_version,
    sidecar_path,
)


class SicarPortalAssistedMetadataTests(SimpleTestCase):
    def test_sidecar_is_read_without_touching_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / 'APP_PE.gpkg'
            snapshot.write_bytes(b'x' * 5000)
            sidecar_path(snapshot).write_text(json.dumps({
                'dataset_slug': 'sicar-app',
                'remote_update_date': '24/08/2026',
                'download_url': 'https://example.invalid/app.gpkg',
            }), encoding='utf-8')
            metadata = read_snapshot_metadata(snapshot)
            self.assertEqual(metadata['dataset_slug'], 'sicar-app')
            self.assertEqual(metadata['remote_update_date'], '24/08/2026')
            self.assertEqual(snapshot.stat().st_size, 5000)

    def test_confirmed_version_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(SICAR_AUTO_INBOX=Path(tmp)):
            record_confirmed_version(
                uf='PE', dataset_slug='sicar-app', remote_update_date='24/08/2026',
                source_url='https://example.invalid/app.gpkg', job_id=10, lote_id=20,
                result_status='CONCLUIDO',
            )
            versions = read_confirmed_versions()
            self.assertEqual(versions['sicar-app']['remote_update_date'], '24/08/2026')
            self.assertEqual(versions['sicar-app']['job_id'], 10)
            self.assertEqual(versions['sicar-app']['lote_id'], 20)
