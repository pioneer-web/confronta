from django.test import SimpleTestCase

from aplicativo.services import CanalConsulta, InteligenciaTerritorialService


class InteligenciaTerritorialServiceTests(SimpleTestCase):
    def test_resumo_e_neutro_de_interface(self):
        resumo = InteligenciaTerritorialService.resumir({
            'imovel': {
                'cod_imovel': 'PE-TESTE',
                'municipio': 'Recife',
                'uf': 'PE',
                'area_total_ha': 123.45,
                'situacao_apresentacao': 'Ativo',
            },
            'restricoes': {'quantidade': 1, 'tipos': ['PRODES']},
            'alertas': {
                'prodes': {'titulo': 'INPE / PRODES', 'estado': 'alerta', 'status': 'Ocorrência'},
                'ibama': {'titulo': 'IBAMA', 'estado': 'ok', 'status': 'Sem indício'},
                'tem_alerta': True,
                'resumo_mapa': 'texto',
                'restricoes': {},
            },
        })
        self.assertEqual(resumo['car'], 'PE-TESTE')
        self.assertEqual(resumo['restricoes']['tipos'], ['PRODES'])
        self.assertEqual(len(resumo['alertas']), 1)
        self.assertEqual(resumo['alertas'][0]['titulo'], 'INPE / PRODES')

    def test_canais_previstos(self):
        self.assertEqual(CanalConsulta.WEB.value, 'WEB')
        self.assertEqual(CanalConsulta.WHATSAPP.value, 'WHATSAPP')
        self.assertEqual(CanalConsulta.APP.value, 'APP')
