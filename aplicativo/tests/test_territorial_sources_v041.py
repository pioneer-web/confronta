from django.test import SimpleTestCase

from administracao.constants import FonteDados
from aplicativo.repositories.territorial import RepositorioTerritorial
from aplicativo.services.consulta_car import ConsultaCarService


class TerritorialSourcesV041Tests(SimpleTestCase):
    def test_funai_e_icmbio_embargo_estao_no_confronto(self):
        funai = RepositorioTerritorial.ANALISES_EXTERNAS['funai']
        self.assertEqual(funai['fonte'], FonteDados.FUNAI)
        self.assertEqual(funai['schema'], 'dados_funai')
        self.assertEqual(funai['tabela'], 'funai_terras_indigenas')
        self.assertTrue(funai['include_full_geometry'])

        icmbio = RepositorioTerritorial.ANALISES_EXTERNAS['icmbio_embargo']
        self.assertEqual(icmbio['fonte'], FonteDados.ICMBIO)
        self.assertEqual(icmbio['schema'], 'dados_icmbio')
        self.assertEqual(icmbio['tabela'], 'icmbio_embargo')
        self.assertTrue(icmbio['include_full_geometry'])

    def test_sicor_envia_geometria_completa_para_o_mapa(self):
        self.assertTrue(
            RepositorioTerritorial.ANALISES_EXTERNAS['sicor_wkt']['include_full_geometry']
        )
        self.assertTrue(
            RepositorioTerritorial.ANALISES_EXTERNAS['sicor_contratadas']['include_full_geometry']
        )

    def test_alertas_novos_entram_no_resumo(self):
        base = {'disponivel': True, 'quantidade': 1, 'registros': [], 'features': [], 'truncada': False}
        vazio = {'disponivel': True, 'quantidade': 0, 'registros': [], 'features': [], 'truncada': False}
        analises = {
            'ibama': vazio,
            'prodes': vazio,
            'assentamentos': vazio,
            'quilombolas': vazio,
            'apa': vazio,
            'sicor': vazio,
            'funai': base,
            'icmbio_embargo': base,
        }
        alertas = ConsultaCarService._montar_alertas(analises, vazio)
        self.assertEqual(alertas['funai']['estado'], 'alerta')
        self.assertEqual(alertas['icmbio_embargo']['estado'], 'alerta')
        self.assertIn('Sobreposição com Terra Indígena', alertas['resumo']['tipos'])
        self.assertIn('Embargo ICMBio', alertas['resumo']['tipos'])

    def test_percentual_da_fonte_e_publicado_no_sicor(self):
        registro = {
            'ref_bacen': 'ABC',
            'nu_ordem': '1',
            'area_geometria_ha': 10,
            'area_sobreposta_ha': 2,
            'percentual_car': 5,
            'percentual_fonte': 20,
        }
        publico = ConsultaCarService._registro_sicor_publico(registro)
        self.assertEqual(publico['percentual_fonte'], 20)
