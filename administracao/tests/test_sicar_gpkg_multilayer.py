from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from administracao.datasets import get_dataset
from administracao.services.dataset_identity import select_dataset_layer
from administracao.services.gis_inspector import inspect_dataset


class SicarGpkgMultilayerTests(SimpleTestCase):
    def test_non_spatial_dictionary_without_crs_does_not_block_gpkg(self):
        with TemporaryDirectory() as tmp:
            gpkg = Path(tmp) / 'PE_AREA_IMOVEL.gpkg'
            gpkg.write_bytes(b'SQLite format 3\x00')

            def fake_info(path, layer=None, **kwargs):
                if layer == 'DICIONARIO':
                    return {
                        'crs': None,
                        'geometry_type': None,
                        'fields': ['CAMPO', 'DESCRICAO'],
                        'dtypes': ['object', 'object'],
                        'features': 25,
                        'encoding': 'UTF-8',
                    }
                return {
                    'crs': 'EPSG:4674',
                    'geometry_type': 'MultiPolygon',
                    'fields': ['COD_IMOVEL', 'NUM_AREA', 'UF'],
                    'dtypes': ['object', 'float64', 'object'],
                    'features': 100,
                    'encoding': 'UTF-8',
                }

            with patch('administracao.services.gis_inspector.pyogrio.list_layers', return_value=[
                ['DICIONARIO', None],
                ['AREA_IMOVEL', 'MultiPolygon'],
            ]), patch('administracao.services.gis_inspector.pyogrio.read_info', side_effect=fake_info):
                layers = inspect_dataset(gpkg)

        self.assertEqual(len(layers), 2)
        dictionary = layers[0]
        spatial = layers[1]
        self.assertTrue(dictionary['auxiliary_table'])
        self.assertFalse(dictionary['is_spatial'])
        self.assertEqual(dictionary['crs'], '')
        self.assertIsNone(dictionary['epsg_detectado'])
        self.assertTrue(spatial['is_spatial'])
        self.assertEqual(spatial['epsg_detectado'], 4674)

    def test_sicar_identity_chooses_spatial_layer_and_ignores_dictionary(self):
        layers = [
            {
                'dataset_name': 'PE_AREA_IMOVEL.gpkg',
                'layer_name': 'DICIONARIO',
                'fields': ['COD_IMOVEL', 'NUM_AREA', 'UF', 'MUNICIPIO'],
                'geometry_type': '',
                'signature': 'dict',
                'auxiliary_table': True,
            },
            {
                'dataset_name': 'PE_AREA_IMOVEL.gpkg',
                'layer_name': 'AREA_IMOVEL',
                'fields': ['COD_IMOVEL', 'NUM_AREA', 'UF', 'MUNICIPIO'],
                'geometry_type': 'MultiPolygon',
                'signature': 'spatial',
                'auxiliary_table': False,
            },
        ]
        spec = get_dataset('sicar-perimetros')
        index, report = select_dataset_layer(layers, spec)
        self.assertEqual(index, 1)
        self.assertEqual(report['camadas_auxiliares_ignoradas'][0]['camada'], 'DICIONARIO')
        self.assertEqual(
            report['camadas_auxiliares_ignoradas'][0]['motivo'],
            'tabela auxiliar não espacial ignorada',
        )
