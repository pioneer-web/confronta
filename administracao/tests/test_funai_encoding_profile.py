from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from administracao.datasets import get_dataset
from administracao.services.gis_inspector import _declared_shapefile_encoding


class FunaiEncodingProfileTests(SimpleTestCase):
    def test_cst_iso_8859_1_is_detected(self):
        with TemporaryDirectory() as tmp:
            shp = Path(tmp) / 'tis_poligonais.shp'
            shp.write_bytes(b'')
            shp.with_suffix('.cst').write_text('ISO-8859-1', encoding='ascii')
            self.assertEqual(_declared_shapefile_encoding(shp), 'ISO-8859-1')

    def test_funai_real_profile_is_operational(self):
        spec = get_dataset('funai-terras-indigenas')
        self.assertIsNotNone(spec)
        self.assertEqual(spec.mode, 'replace_table')
        self.assertEqual(spec.stable_table, 'funai_terras_indigenas')
        self.assertEqual(spec.raw_table, 'raw_funai_terras_indigenas')
        canonicals = {field.canonical for field in spec.fields}
        self.assertIn('terrai_cod', canonicals)
        self.assertIn('terrai_nom', canonicals)
        self.assertIn('data_atualizacao', canonicals)
        self.assertIn('epsg_declarado', canonicals)
