from pathlib import Path

from django.test import SimpleTestCase

from administracao.datasets import get_dataset


class SourcesV030ContractTests(SimpleTestCase):
    def test_sicar_is_manual_only_in_compose(self):
        root = Path(__file__).resolve().parents[2]
        compose = (root / 'docker-compose.yml').read_text(encoding='utf-8')
        settings = (root / 'config' / 'settings.py').read_text(encoding='utf-8')
        self.assertNotIn('sicar_monitor:', compose)
        self.assertNotIn('SICAR_AUTOMATION_ENABLED', settings)
        self.assertNotIn('source_monitor:', compose)

    def test_ibama_and_incra_keys_are_stable(self):
        self.assertEqual(get_dataset('ibama-termos-embargo').unique_key, ('seq_tad',))
        self.assertEqual(get_dataset('incra-sigef-parcelas').unique_key, ('parcela_co',))
        self.assertEqual(get_dataset('incra-snci-certificados').unique_key, ('num_certif',))

    def test_client_contract_only_returns_intersections(self):
        root = Path(__file__).resolve().parents[2]
        sql_contract = (root / 'administracao' / 'services' / 'confronta_contract.py').read_text(encoding='utf-8')
        self.assertIn('ST_Intersects', sql_contract)
        self.assertIn('ST_Intersection', sql_contract)
        self.assertIn('area_sobreposicao_ha', sql_contract)
        self.assertIn('alertas_ibama_por_car', sql_contract)
        self.assertIn('alertas_incra_por_car', sql_contract)
        self.assertIn('detalhes_ibama', sql_contract)

    def test_ibama_v04_uses_bulk_official_files_not_pamgia(self):
        root = Path(__file__).resolve().parents[2]
        settings = (root / 'config' / 'settings.py').read_text(encoding='utf-8')
        sync = (root / 'administracao' / 'services' / 'source_sync.py').read_text(encoding='utf-8')
        bulk = (root / 'administracao' / 'services' / 'ibama_bulk_sync.py').read_text(encoding='utf-8')
        self.assertNotIn('IBAMA_PAMGIA_FEATURE_URL', settings)
        self.assertIn('IBAMA_TERMO_EMBARGO_URL', settings)
        self.assertIn('termo_embargo_csv.zip', settings)
        self.assertIn('bulk_dados_abertos_v04', bulk)
        self.assertIn('process_ibama_bulk_job', sync)
        self.assertNotIn('FeatureServer/2', sync)
