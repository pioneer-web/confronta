from pathlib import Path

from django.test import SimpleTestCase


class IbamaPamgiaRetiredTests(SimpleTestCase):
    def test_pamgia_is_not_in_v04_ingestion_path(self):
        root = Path(__file__).resolve().parents[2]
        settings = (root / 'config' / 'settings.py').read_text(encoding='utf-8')
        sync = (root / 'administracao' / 'services' / 'source_sync.py').read_text(encoding='utf-8')
        self.assertNotIn('IBAMA_PAMGIA_FEATURE_URL', settings)
        self.assertNotIn('FeatureServer/2', sync)
        self.assertIn('process_ibama_bulk_job', sync)
