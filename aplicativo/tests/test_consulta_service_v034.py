from django.test import SimpleTestCase

from administracao.constants import FonteDados
from aplicativo.repositories import RepositorioTerritorial
from aplicativo.services import ConsultaCarService


class FakeRepositorio:
    def __init__(self):
        self.calls = []

    def buscar_imovel_por_car(self, car):
        self.calls.append(('imovel', car))
        return {
            'cod_imovel': car,
            'situacao_car': 'ATIVO',
            'geometry': {'type': 'Polygon', 'coordinates': []},
        }

    def buscar_camadas_sicar(self, car):
        self.calls.append(('sicar', car))
        return {'app': {'label': 'APP', 'disponivel': True, 'features': []}}

    def buscar_analises_externas(self, car):
        self.calls.append(('externas', car))
        vazio = {'disponivel': True, 'quantidade': 0, 'features': [], 'registros': []}
        return {
            'ibama': {**vazio, 'label': 'IBAMA'},
            'prodes': {**vazio, 'label': 'PRODES'},
            'assentamentos': {**vazio, 'label': 'Assentamentos'},
            'quilombolas': {**vazio, 'label': 'Quilombolas'},
            'apa': {**vazio, 'label': 'APA'},
        }

    def buscar_sobreposicoes_outros_cars(self, car):
        self.calls.append(('outros_cars', car))
        return {'label': 'Sobreposição com outros CARs', 'disponivel': True, 'quantidade': 0, 'features': [], 'registros': []}


class ConsultaCarServiceV034Tests(SimpleTestCase):
    def test_consulta_agrega_sicar_fontes_externas_e_outros_cars(self):
        repo = FakeRepositorio()
        car = 'PE-2614105-9C74D4EF908C4BF4A177617BDC9C3D86'
        result = ConsultaCarService(repositorio=repo).executar(car)

        self.assertIn('imovel', result)
        self.assertIn('camadas', result)
        self.assertIn('analises_externas', result)
        self.assertIn('camadas_externas', result)
        self.assertIn('restricoes', result)
        self.assertEqual(result['imovel']['situacao_apresentacao'], 'Ativo')
        self.assertEqual(
            repo.calls,
            [('imovel', car), ('sicar', car), ('externas', car), ('outros_cars', car)],
        )

    def test_modelagem_externa_usa_tabelas_operacionais_confirmadas(self):
        cfg = RepositorioTerritorial.ANALISES_EXTERNAS
        self.assertEqual(cfg['ibama']['tabela'], 'ibama_embargo')
        self.assertEqual(cfg['ibama']['fonte'], FonteDados.IBAMA)
        self.assertEqual(cfg['prodes']['tabela'], 'prodes_ocorrencia')
        self.assertEqual(cfg['assentamentos']['tabela'], 'incra_assentamentos')
        self.assertEqual(cfg['quilombolas']['tabela'], 'incra_areas_quilombolas')
        self.assertEqual(cfg['apa_cnuc']['tabela'], 'cnuc_unidade_conservacao')
        self.assertEqual(cfg['apa_icmbio']['tabela'], 'icmbio_unidade_conservacao_federal')

    def test_sicar_completo_inclui_camadas_adicionais(self):
        camadas = RepositorioTerritorial.CAMADAS_SICAR
        for chave in ('area_pousio', 'hidrografia', 'servidao_administrativa', 'uso_restrito'):
            self.assertIn(chave, camadas)

    def test_restricoes_na_faixa_nao_contam_ibama_nem_apa(self):
        vazio = {'disponivel': True, 'quantidade': 0, 'features': [], 'registros': []}
        positivo = {'disponivel': True, 'quantidade': 1, 'features': [], 'registros': [{'area_sobreposta_ha': 2.0}]}
        alertas = ConsultaCarService._montar_alertas({
            'ibama': positivo,
            'prodes': positivo,
            'assentamentos': positivo,
            'quilombolas': vazio,
            'apa': positivo,
        }, positivo)
        self.assertEqual(alertas['restricoes']['quantidade'], 3)
        self.assertEqual(
            alertas['restricoes']['tipos'],
            ['Sobreposição com outro CAR', 'Assentamento INCRA', 'PRODES'],
        )
        self.assertEqual(alertas['ibama']['status'], 'Provável restrição')
    def test_ibama_e_conservador_e_nao_expoe_dados_pessoais(self):
        resultado = {
            'disponivel': True,
            'quantidade': 1,
            'truncada': False,
            'registros': [{
                'numero_embargo': '123-E',
                'processo': '02000.000000/2026-00',
                'nome_embargado': 'DADO QUE NAO DEVE SAIR',
                'cpf_cnpj_embargado': '00000000000',
                'area_sobreposta_ha': 2.5,
            }],
        }
        alerta = ConsultaCarService._alerta_ibama(resultado)
        self.assertEqual(alerta['status'], 'Provável restrição')
        self.assertTrue(alerta['confirmacao_externa_necessaria'])
        self.assertNotIn('nome_embargado', alerta['registros'][0])
        self.assertNotIn('cpf_cnpj_embargado', alerta['registros'][0])

    def test_prodes_reservatorio_nao_ativa_alerta_principal(self):
        resultado = ConsultaCarService._preparar_prodes({
            'disponivel': True,
            'registros': [
                {'main_class': 'RESERVATORIO', 'area_sobreposta_ha': 3.0},
                {'main_class': 'DESMATAMENTO', 'year': 2025, 'area_sobreposta_ha': 2.0},
            ],
            'features': [],
        })
        self.assertEqual(resultado['quantidade'], 1)
        self.assertEqual(resultado['tipos'], ['DESMATAMENTO'])
        self.assertEqual(resultado['anos'], ['2025'])

