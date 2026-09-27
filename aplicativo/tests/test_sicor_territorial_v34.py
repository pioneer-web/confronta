from django.test import TestCase
from unittest.mock import patch

from aplicativo.repositories import RepositorioTerritorial
from aplicativo.services import ConsultaCarService


class SicorTerritorialV34Tests(TestCase):
    def setUp(self):
        self.vazio = {'disponivel': True, 'quantidade': 0, 'features': [], 'registros': []}
        self.sicor = {
            'disponivel': True,
            'quantidade': 1,
            'features': [{
                'type': 'Feature',
                'properties': {
                    'ref_bacen': 'REF-1', 'nu_ordem': 1, 'ano_sicor': 2026,
                    'area_sobreposta_ha': 12.5,
                },
                'geometry': {'type': 'Polygon', 'coordinates': []},
            }],
            'registros': [{
                'ref_bacen': 'REF-1', 'nu_ordem': 1, 'indice_gleba': 2,
                'ano_sicor': 2026, 'vl_parc_credito': 100000.0,
                'area_sobreposta_ha': 12.5, 'gt_geometria': 'NAO_EXPOR',
            }],
            'area_unica_sobreposta_ha': 12.5,
        }

    def test_repositorio_modela_as_duas_camadas_espaciais_sicor(self):
        cfg = RepositorioTerritorial.ANALISES_EXTERNAS
        self.assertEqual(cfg['sicor_wkt']['tabela'], 'sicor_glebas_wkt')
        self.assertEqual(cfg['sicor_wkt']['geometry_column'], 'geom')
        self.assertEqual(cfg['sicor_contratadas']['tabela'], 'sicor_glebas_contratadas')
        self.assertEqual(cfg['sicor_contratadas']['geometry_column'], 'geom')

    def test_sicor_nao_entra_nos_alertas(self):
        alertas = ConsultaCarService._montar_alertas({
            'ibama': self.vazio,
            'prodes': self.vazio,
            'assentamentos': self.vazio,
            'quilombolas': self.vazio,
            'apa': self.vazio,
            'sicor': self.sicor,
        }, self.vazio)
        self.assertNotIn('sicor', alertas)
        self.assertNotIn('Crédito rural — SICOR', alertas['resumo']['tipos'])
        self.assertFalse(alertas['tem_alerta'])

    def test_sicor_entra_como_camada_externa(self):
        camadas = ConsultaCarService._montar_camadas_externas({
            'ibama': self.vazio,
            'prodes': self.vazio,
            'assentamentos': self.vazio,
            'quilombolas': self.vazio,
            'apa': self.vazio,
            'sicor': self.sicor,
        }, self.vazio)
        self.assertIn('sicor', camadas)
        self.assertTrue(camadas['sicor']['disponivel'])
        self.assertEqual(camadas['sicor']['label'], 'SICOR / Glebas')
        self.assertEqual(len(camadas['sicor']['features']), 1)

    def test_validacao_uf_atual_preserva_gleba_com_uf_compativel(self):
        repository = RepositorioTerritorial.__new__(RepositorioTerritorial)
        wkt = {'disponivel': True, 'features': [{
            'type': 'Feature', 'geometry': {'type': 'Polygon', 'coordinates': []},
            'properties': {'ref_bacen': '519338239', 'nu_ordem': 1, 'nu_indice': 1, '_ano_arquivo': 2025},
        }], 'registros': []}
        empty = {'disponivel': False, 'features': [], 'registros': []}
        with patch.object(repository, '_buscar_dados_operacoes_sicor', return_value={
            ('519338239', 1, 2025): {'cd_estado': 'PE'},
        }):
            result = repository._combinar_sicor_fontes(wkt, empty, uf_car='PE')
        self.assertEqual(len(result['features']), 1)
        self.assertEqual(result['registros'][0]['validacao_uf_sicor'], 'VALIDADA')

    def test_validacao_uf_descarta_gleba_incompativel(self):
        repository = RepositorioTerritorial.__new__(RepositorioTerritorial)
        wkt = {'disponivel': True, 'features': [{
            'type': 'Feature', 'geometry': {'type': 'Polygon', 'coordinates': []},
            'properties': {'ref_bacen': '2309549', 'nu_ordem': 1, 'nu_indice': 1, '_ano_arquivo': 2013},
        }], 'registros': []}
        empty = {'disponivel': False, 'features': [], 'registros': []}
        with patch.object(repository, '_buscar_dados_operacoes_sicor', return_value={
            ('2309549', 1, 2013): {'cd_estado': 'RS'},
        }):
            result = repository._combinar_sicor_fontes(wkt, empty, uf_car='PE')
        self.assertEqual(result['features'], [])
        self.assertEqual(result['registros'], [])

    def test_campos_de_operacao_sao_projetados_em_feature_properties(self):
        repository = RepositorioTerritorial.__new__(RepositorioTerritorial)
        properties = {'ref_bacen': '519338239', 'nu_ordem': 1, 'nu_indice': 1, '_ano_arquivo': 2025}
        wkt = {'disponivel': True, 'features': [{
            'type': 'Feature', 'geometry': {'type': 'Polygon', 'coordinates': []}, 'properties': properties,
        }], 'registros': []}
        empty = {'disponivel': False, 'features': [], 'registros': []}
        enrichment = {
            'cd_estado': 'PE', 'nome_instituicao': 'BANCO', 'segmento_instituicao': 'COOPERATIVA',
            'nome_programa': 'Programa', 'dt_emissao': '2025-01-01', 'dt_vencimento': '2026-01-01',
            'vl_parc_credito': 10, 'vl_area_financ': 2,
        }
        with patch.object(repository, '_buscar_dados_operacoes_sicor', return_value={
            ('519338239', 1, 2025): enrichment,
        }):
            result = repository._combinar_sicor_fontes(wkt, empty, uf_car='PE')
        self.assertTrue(set(enrichment) <= result['features'][0]['properties'].keys())
