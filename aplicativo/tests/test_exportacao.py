from django.test import SimpleTestCase

from aplicativo.services.exportacao import ExportacaoKmlService


class FakeRepositorio:
    def buscar_imovel_por_car(self, car):
        return {
            'cod_imovel': car,
            'area_total_ha': 10.5,
            'uf': 'PE',
            'municipio': 'Sertânia',
            'geometry': {
                'type': 'Polygon',
                'coordinates': [[[-37.3, -8.0], [-37.2, -8.0], [-37.2, -8.1], [-37.3, -8.0]]],
            },
        }

    def buscar_camada_para_exportacao(self, car, chave):
        return {
            'label': 'APP',
            'features': [{
                'type': 'Feature',
                'properties': {'area_ha': 2.5},
                'geometry': {
                    'type': 'Polygon',
                    'coordinates': [[[-37.3, -8.0], [-37.2, -8.0], [-37.2, -8.1], [-37.3, -8.0]]],
                },
            }],
        }


class ExportacaoKmlTests(SimpleTestCase):
    CAR = 'PE-2614105-9C74D4EF908C4BF4A177617BDC9C3D86'

    def test_exporta_perimetro_em_kml(self):
        conteudo, car = ExportacaoKmlService(FakeRepositorio()).exportar_imovel(self.CAR)
        texto = conteudo.decode('utf-8')
        self.assertEqual(car, self.CAR)
        self.assertIn('<Polygon>', texto)
        self.assertIn(self.CAR, texto)

    def test_exporta_camada_em_kml(self):
        conteudo, label, car = ExportacaoKmlService(FakeRepositorio()).exportar_camada(self.CAR, 'app')
        texto = conteudo.decode('utf-8')
        self.assertEqual(label, 'APP')
        self.assertEqual(car, self.CAR)
        self.assertIn('APP 1', texto)
