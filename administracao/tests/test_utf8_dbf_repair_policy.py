from django.test import SimpleTestCase

from administracao.services.dbf_sanitizer import prepare_utf8_shapefile_for_import


class UTF8DBFRepairPolicyTests(SimpleTestCase):
    def test_non_shapefile_is_not_modified(self):
        layer = {
            'dataset_path': '/tmp/base.gpkg',
            'source_encoding': 'UTF-8',
        }
        report = prepare_utf8_shapefile_for_import(layer, '/tmp', enabled=True)
        self.assertFalse(report['aplicado'])
        self.assertIn('não é Shapefile', report['motivo'])

    def test_non_utf8_declared_shapefile_is_not_rewritten(self):
        layer = {
            'dataset_path': '/tmp/inexistente.shp',
            'source_encoding': 'CP1252',
        }
        # Como o arquivo não existe, a função também deve sair sem qualquer mutação.
        report = prepare_utf8_shapefile_for_import(layer, '/tmp', enabled=True)
        self.assertFalse(report['aplicado'])
