from django.test import SimpleTestCase

from administracao.datasets import get_dataset
from administracao.services.schema_drift import compare_schema, snapshot_layer


class SchemaDriftTests(SimpleTestCase):
    def test_detecta_nome_geometria_e_precisao_numerica(self):
        previous = snapshot_layer({
            'layer_name': 'AREA_IMOVEL_1',
            'dataset_name': 'AREA_IMOVEL.shp',
            'geometry_type': 'Polygon',
            'epsg_detectado': 4674,
            'signature': 'old',
            'field_definitions': [
                {'name': 'COD_IMOVEL', 'ogr_type': 'String', 'width': 50, 'precision': 0, 'position': 0},
                {'name': 'MOD_FISCAL', 'ogr_type': 'Real', 'width': 10, 'precision': 2, 'position': 1},
            ],
        })
        current = snapshot_layer({
            'layer_name': 'IMOVEL_RURAL',
            'dataset_name': 'IMOVEL_RURAL.shp',
            'geometry_type': 'MultiPolygon',
            'epsg_detectado': 4674,
            'signature': 'new',
            'field_definitions': [
                {'name': 'COD_IMOVEL', 'ogr_type': 'String', 'width': 50, 'precision': 0, 'position': 0},
                {'name': 'MOD_FISCAL', 'ogr_type': 'Real', 'width': 33, 'precision': 31, 'position': 1},
            ],
        })
        report = compare_schema(previous, current, get_dataset('sicar-perimetros'))
        types = {item['type'] for item in report['changes']}
        self.assertTrue(report['changed'])
        self.assertIn('LAYER_RENAMED', types)
        self.assertIn('NUMERIC_PRECISION_CHANGED', types)
        self.assertIn('GEOMETRY_TYPE_CHANGED', types)

    def test_primeira_importacao_vira_baseline_sem_alerta_de_mudanca(self):
        current = snapshot_layer({
            'layer_name': 'AREA_IMOVEL_1',
            'dataset_name': 'AREA_IMOVEL.shp',
            'geometry_type': 'Polygon',
            'epsg_detectado': 4674,
            'signature': 'new',
            'fields': ['COD_IMOVEL'],
            'dtypes': ['object'],
        })
        report = compare_schema(None, current, get_dataset('sicar-perimetros'))
        self.assertTrue(report['baseline'])
        self.assertFalse(report['changed'])
