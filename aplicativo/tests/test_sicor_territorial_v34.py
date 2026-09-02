from django.test import SimpleTestCase

from aplicativo.repositories import RepositorioTerritorial
from aplicativo.services import ConsultaCarService


class SicorTerritorialV34Tests(SimpleTestCase):
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

    def test_sicor_entra_nos_alertas(self):
        alertas = ConsultaCarService._montar_alertas({
            'ibama': self.vazio,
            'prodes': self.vazio,
            'assentamentos': self.vazio,
            'quilombolas': self.vazio,
            'apa': self.vazio,
            'sicor': self.sicor,
        }, self.vazio)
        self.assertEqual(alertas['sicor']['status'], 'Identificado')
        self.assertEqual(alertas['sicor']['layer_key'], 'ext_sicor')
        self.assertIn('Crédito rural — SICOR', alertas['resumo']['tipos'])
        self.assertNotIn('gt_geometria', alertas['sicor']['registros'][0])

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
        self.assertEqual(camadas['sicor']['label'], 'SICOR / Crédito Rural')
        self.assertEqual(len(camadas['sicor']['features']), 1)
