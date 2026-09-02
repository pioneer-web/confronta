from django.test import SimpleTestCase

from administracao.services.normalization import geometry_family as normalization_geometry_family
from administracao.services.schema_drift import geometry_family as schema_geometry_family
from administracao.services.dataset_identity import geometry_family as identity_geometry_family
from administracao.services.postgis import build_ogr2ogr_command


class SicarMultiSurfaceNormalizationTests(SimpleTestCase):
    def test_multisurface_is_polygon_family_everywhere(self):
        for fn in (normalization_geometry_family, schema_geometry_family, identity_geometry_family):
            self.assertEqual(fn('MultiSurface'), 'polygon')
            self.assertEqual(fn('CurvePolygon'), 'polygon')

    def test_staging_ogr2ogr_linearizes_and_promotes_polygonal_surface(self):
        layer = {
            'geometry_type': 'MultiSurface',
            'dataset_path': '/tmp/AREA_IMOVEL_PE.gpkg',
            'layer_name': 'AREA_IMOVEL_PE',
        }
        command = build_ogr2ogr_command(layer, 'stg_test')
        joined = ' '.join(command)
        self.assertIn('-nlt CONVERT_TO_LINEAR', joined)
        self.assertIn('-nlt PROMOTE_TO_MULTI', joined)


    def test_staging_command_uses_expected_polygon_family_when_metadata_is_generic(self):
        layer = {
            'geometry_type': 'Geometry',
            'expected_geometry_families': ['polygon'],
            'dataset_path': '/tmp/AREA_IMOVEL_PE.gpkg',
            'layer_name': 'AREA_IMOVEL_PE',
        }
        command = build_ogr2ogr_command(layer, 'stg_test')
        joined = ' '.join(command)
        self.assertIn('-nlt CONVERT_TO_LINEAR', joined)
        self.assertIn('-nlt PROMOTE_TO_MULTI', joined)
