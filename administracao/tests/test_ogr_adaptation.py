from unittest.mock import patch

from django.test import SimpleTestCase

from administracao.services.postgis import build_ogr2ogr_command, _repair_geometry_expression


class OGRAdaptationTests(SimpleTestCase):
    @patch('administracao.services.postgis._pg_ogr_connection', return_value='PG:dbname=test')
    def test_poligono_desativa_precision_e_promove_multi(self, _):
        command = build_ogr2ogr_command({
            'dataset_path': '/tmp/AREA_IMOVEL.shp',
            'layer_name': 'AREA_IMOVEL_1',
            'geometry_type': 'Polygon',
        }, 'stg_test')
        joined = ' '.join(command)
        self.assertIn('PRECISION=NO', joined)
        self.assertIn('PROMOTE_TO_MULTI', joined)
        self.assertNotIn('-skipfailures', command)

    @patch('administracao.services.postgis._pg_ogr_connection', return_value='PG:dbname=test')
    def test_ponto_nao_e_promovido_para_multi(self, _):
        command = build_ogr2ogr_command({
            'dataset_path': '/tmp/pontos.gpkg',
            'layer_name': 'pontos',
            'geometry_type': 'Point',
        }, 'stg_test')
        self.assertIn('PRECISION=NO', ' '.join(command))
        self.assertNotIn('PROMOTE_TO_MULTI', command)


    @patch('administracao.services.postgis._pg_ogr_connection', return_value='PG:dbname=test')
    def test_nome_raw_no_staging_e_especifico_por_dataset(self, _):
        command = build_ogr2ogr_command({
            'dataset_path': '/tmp/AREA_POUSIO.shp',
            'layer_name': 'AREA_POUSIO_1',
            'geometry_type': 'Polygon',
        }, 'stg_test', target_table='raw_sicar_area_pousio')
        self.assertIn('stg_test.raw_sicar_area_pousio', command)

    def test_reparo_polygon_usa_makevalid_sem_skipfailures(self):
        expr = _repair_geometry_expression('geom', 'Polygon').as_string(None)
        self.assertIn('ST_MakeValid', expr)
        self.assertIn('ST_CollectionExtract', expr)
        self.assertIn('ST_Multi', expr)


    def test_stderr_gdal_cp1252_nao_derruba_decoder(self):
        from administracao.services.postgis import _decode_subprocess_output
        raw = 'camada CNUC com órgão e proteção'.encode('cp1252')
        decoded = _decode_subprocess_output(raw)
        self.assertIn('órgão', decoded)
        self.assertIn('proteção', decoded)

    @patch('administracao.services.postgis._pg_ogr_connection', return_value='PG:dbname=test')
    def test_encoding_override_detectado_e_repassado_ao_ogr(self, _):
        command = build_ogr2ogr_command({
            'dataset_path': '/tmp/cnuc.shp',
            'layer_name': 'cnuc_2024_02',
            'geometry_type': 'Polygon',
            'encoding_override': 'CP1252',
        }, 'stg_test')
        self.assertIn('ENCODING=CP1252', command)


class DBFEncodingRepairTests(SimpleTestCase):
    def test_utf8_truncado_no_limite_dbf_e_reparado_sem_mudar_largura(self):
        from administracao.services.dbf_sanitizer import _replace_invalid_utf8_bytes

        raw = 'Regulamentaç'.encode('utf-8') + b'\xef'
        fixed, replacements = _replace_invalid_utf8_bytes(raw)

        self.assertEqual(len(fixed), len(raw))
        self.assertEqual(replacements, 1)
        self.assertEqual(fixed.decode('utf-8'), 'Regulamentaç?')

    def test_texto_utf8_valido_nao_e_alterado(self):
        from administracao.services.dbf_sanitizer import _replace_invalid_utf8_bytes

        raw = 'Proteção Ambiental'.encode('utf-8')
        fixed, replacements = _replace_invalid_utf8_bytes(raw)

        self.assertEqual(fixed, raw)
        self.assertEqual(replacements, 0)
