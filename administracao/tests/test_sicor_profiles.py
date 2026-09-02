from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from administracao.datasets import get_dataset, datasets_for_source
from administracao.forms import ImportacaoLoteForm, UploadBaseForm
from administracao.source_catalog import get_source_profile


class SicorProfileTests(SimpleTestCase):
    def test_sicor_has_six_profiles(self):
        self.assertEqual(
            {d.slug for d in datasets_for_source('sicor')},
            {
                'sicor-operacao-basica',
                'sicor-complemento-operacao-basica',
                'sicor-glebas-contratadas',
                'sicor-glebas-wkt',
                'sicor-propriedades',
                'sicor-mutuarios',
            },
        )

    def test_glebas_contratadas_uses_point_sequence_sirgas2000(self):
        spec = get_dataset('sicor-glebas-contratadas')
        self.assertEqual(spec.data_kind, 'sicor_gleba_points')
        self.assertFalse(spec.year_partitioned)
        self.assertEqual(spec.geometry_srid, 4674)
        self.assertIn('sicor_glebas_contrat', spec.filename_patterns)

    def test_glebas_wkt_uses_confirmed_sirgas2000(self):
        spec = get_dataset('sicor-glebas-wkt')
        self.assertEqual(spec.data_kind, 'sicor_wkt')
        self.assertTrue(spec.year_partitioned)
        self.assertEqual(spec.geometry_wkt_field, 'gt_geometria')
        self.assertEqual(spec.geometry_srid, 4674)

    def test_sicor_catalog_is_importable_per_profile(self):
        source = get_source_profile('sicor')
        self.assertTrue(source.is_importable)
        self.assertEqual(source.implementation, 'OPERACIONAL')
        self.assertEqual(
            {item.dataset_slug for item in source.items},
            {d.slug for d in datasets_for_source('sicor')},
        )

    def test_incra_items_have_direct_import_targets(self):
        source = get_source_profile('incra')
        targets = {item.code: item.dataset_slug for item in source.items}
        self.assertEqual(targets['ASSENTAMENTOS'], 'incra-assentamentos')
        self.assertEqual(targets['QUILOMBOLAS'], 'incra-quilombolas')

    def test_sicor_upload_accepts_gzip_and_csv_only(self):
        gz = SimpleUploadedFile('SICOR_PROPRIEDADES.gz', b'\x1f\x8bfake')
        form = UploadBaseForm(files={'arquivo': gz}, source_slug='sicor', dataset_slug='sicor-propriedades')
        self.assertTrue(form.is_valid(), form.errors)

        csv_file = SimpleUploadedFile('SICOR_PROPRIEDADES.csv', b'REF_BACEN;NU_ORDEM;CD_CAR\n1;1;ABC')
        form = UploadBaseForm(files={'arquivo': csv_file}, source_slug='sicor', dataset_slug='sicor-propriedades')
        self.assertTrue(form.is_valid(), form.errors)

        zip_file = SimpleUploadedFile('SICOR_PROPRIEDADES.zip', b'PK')
        form = UploadBaseForm(files={'arquivo': zip_file}, source_slug='sicor', dataset_slug='sicor-propriedades')
        self.assertFalse(form.is_valid())

    def test_sicor_is_not_offered_in_generic_gis_batch(self):
        choices = dict(ImportacaoLoteForm().fields['fonte'].choices)
        self.assertNotIn('sicor', choices)

from pathlib import Path
from tempfile import TemporaryDirectory
from administracao.services.sicor_import import _detect_csv_format, _validate_identity, _polygonal_geometry


class SicorParserTests(SimpleTestCase):
    def test_properties_header_is_confirmed_and_extra_is_preserved(self):
        spec = get_dataset('sicor-propriedades')
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'SICOR_PROPRIEDADES.csv'
            path.write_text(
                'REF_BACEN;NU_ORDEM;CD_CNPJ_CPF;CD_SNCR;CD_NIRF;CD_CAR;CAMPO_NOVO\n'
                '123;1;000;456;789;PE-TESTE;valor\n',
                encoding='utf-8',
            )
            info = _detect_csv_format(path, spec)
            identity = _validate_identity(spec, path.name, info)
        self.assertEqual(identity['status'], 'CONFIRMADO')
        self.assertIn('campo_novo', identity['campos_extras_preservados_raw'])

    def test_glebas_contratadas_real_header_is_confirmed(self):
        spec = get_dataset('sicor-glebas-contratadas')
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'SICOR_GLEBAS_CONTRAT.csv'
            path.write_text(
                '#REF_BACEN;NU_ORDEM;NU_IDENTIFICADOR;NU_INDICE_GLEBA;NU_INDICE_PONTO;VL_LATITUDE;VL_LONGITUDE;CGL_VL_ALTITUDE;ID_PONTO\n'
                '520381361;1;1;0;0;-28.169870;-49.984506;0.00;2149964558085000\n',
                encoding='utf-8',
            )
            info = _detect_csv_format(path, spec)
            identity = _validate_identity(spec, path.name, info)
        self.assertEqual(identity['status'], 'CONFIRMADO')
        self.assertEqual(identity['mapeamento']['ref_bacen'], 'ref_bacen')
        self.assertEqual(identity['mapeamento']['vl_latitude'], 'vl_latitude')

    def test_polygon_wkt_is_normalized_to_multipolygon(self):
        wkt_value, reason, repaired = _polygonal_geometry('POLYGON((-40 -10,-39 -10,-39 -9,-40 -9,-40 -10))')
        self.assertTrue(wkt_value.startswith('MULTIPOLYGON'))
        self.assertEqual(reason, '')
        self.assertFalse(repaired)
