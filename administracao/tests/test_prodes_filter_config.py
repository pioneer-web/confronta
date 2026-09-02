from django.test import SimpleTestCase
from django.utils import timezone

from administracao.forms import ImportacaoLoteForm, UploadBaseForm
from administracao.services.exceptions import GISValidationError
from administracao.services.prodes_filter import DEFAULT_PRODES_START_YEAR, normalize_prodes_start_year


class ProdesFilterConfigTests(SimpleTestCase):
    def test_default_year_is_2019(self):
        self.assertEqual(normalize_prodes_start_year(None), DEFAULT_PRODES_START_YEAR)

    def test_year_before_2019_is_rejected(self):
        with self.assertRaises(GISValidationError):
            normalize_prodes_start_year(2018)

    def test_batch_form_accepts_configurable_prodes_year(self):
        form = ImportacaoLoteForm(
            data={'fonte': 'prodes', 'ano_inicial': '2022'},
            files={},
            fonte_locked='prodes',
        )
        form.is_valid()
        self.assertNotIn('ano_inicial', form.errors)
        self.assertEqual(form.cleaned_data.get('ano_inicial'), 2022)

    def test_batch_form_rejects_future_year(self):
        form = ImportacaoLoteForm(
            data={'fonte': 'prodes', 'ano_inicial': str(timezone.localdate().year + 1)},
            files={},
            fonte_locked='prodes',
        )
        form.is_valid()
        self.assertIn('ano_inicial', form.errors)

    def test_single_upload_form_exposes_year_only_for_prodes(self):
        self.assertIn('ano_inicial', UploadBaseForm(source_slug='prodes').fields)
        self.assertNotIn('ano_inicial', UploadBaseForm(source_slug='sicar').fields)

    def test_unlocked_batch_form_keeps_sicar_state_field_for_dynamic_ui(self):
        form = ImportacaoLoteForm()
        self.assertIn('uf', form.fields)
        self.assertIn('ano_inicial', form.fields)
