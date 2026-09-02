from django.test import SimpleTestCase

from administracao.forms import ImportacaoLoteForm, UploadBaseForm


class ProdesDuplicateControlTests(SimpleTestCase):
    def test_single_upload_does_not_mix_delete_with_import(self):
        form = UploadBaseForm(source_slug='prodes')
        self.assertNotIn('limpar_antes_importar', form.fields)

    def test_batch_upload_does_not_mix_delete_with_import(self):
        form = ImportacaoLoteForm(fonte_locked='prodes')
        self.assertNotIn('limpar_antes_importar', form.fields)

    def test_prodes_temporal_filter_remains_available(self):
        form = ImportacaoLoteForm(fonte_locked='prodes')
        self.assertIn('ano_inicial', form.fields)

    def test_sicar_does_not_expose_prodes_year(self):
        form = ImportacaoLoteForm(fonte_locked='sicar')
        self.assertNotIn('ano_inicial', form.fields)
