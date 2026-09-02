from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from administracao.constants import BATCH_FONTE_SLUGS, FONTE_SLUGS
from administracao.datasets import get_dataset
from administracao.forms import UploadBaseForm
from administracao.source_catalog import get_source_profile


class ManualPublicSourcesTests(SimpleTestCase):
    def test_expected_sources_are_manual_importable(self):
        slugs = {
            'sigef', 'sncr',
        }
        for slug in slugs:
            source = get_source_profile(slug)
            self.assertIsNotNone(source, slug)
            self.assertTrue(source.is_importable, slug)
            self.assertEqual(source.implementation, 'RAW_FLEXIVEL')

    def test_removed_sources_are_not_exposed_for_import(self):
        for slug in ('ana', 'focos-calor', 'florestas-publicas', 'deter', 'anm', 'zarc', 'mapbiomas'):
            self.assertIsNone(get_source_profile(slug), slug)
            self.assertNotIn(slug, FONTE_SLUGS)
            self.assertNotIn(slug, BATCH_FONTE_SLUGS)
        self.assertIsNone(get_dataset('ana-outorgas'))
        self.assertIsNone(get_dataset('focos-calor'))
        for dataset_slug in (
            'florestas-publicas-cnfp', 'deter-alertas', 'anm-processos-minerarios',
            'zarc-tabua-risco', 'mapbiomas-alertas',
        ):
            self.assertIsNone(get_dataset(dataset_slug))

    def test_raw_profiles_do_not_invent_operational_mapping(self):
        for slug in (
            'sigef-parcelas', 'sncr-dados-abertos',
        ):
            spec = get_dataset(slug)
            self.assertIsNotNone(spec, slug)
            self.assertEqual(spec.mode, 'raw_only')
            self.assertEqual(spec.stable_table, spec.raw_table)
            self.assertEqual(tuple(spec.fields), ())

    def test_sncr_accepts_csv(self):
        upload = SimpleUploadedFile('sncr.csv', b'a;b\n1;2\n', content_type='text/csv')
        form = UploadBaseForm(
            data={}, files={'arquivo': upload},
            source_slug='sncr', dataset_slug='sncr-dados-abertos',
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_funai_profile_is_operational_and_accepts_official_zip(self):
        spec = get_dataset('funai-terras-indigenas')
        self.assertIsNotNone(spec)
        self.assertEqual(spec.mode, 'replace_table')
        self.assertEqual(spec.stable_table, 'funai_terras_indigenas')
        self.assertEqual(spec.raw_table, 'raw_funai_terras_indigenas')
        self.assertTrue(any(f.canonical == 'terrai_cod' and f.required for f in spec.fields))
        self.assertTrue(any(f.canonical == 'terrai_nom' and f.required for f in spec.fields))

        upload = SimpleUploadedFile('tis_poligonais.zip', b'placeholder', content_type='application/zip')
        form = UploadBaseForm(
            data={}, files={'arquivo': upload},
            source_slug='funai', dataset_slug='funai-terras-indigenas',
        )
        self.assertTrue(form.is_valid(), form.errors)
