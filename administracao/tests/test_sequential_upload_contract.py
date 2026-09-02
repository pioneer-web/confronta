from django.test import SimpleTestCase

from administracao.forms import ImportacaoLoteForm


class SequentialUploadContractTests(SimpleTestCase):
    def test_normal_batch_form_exposes_multiple_files_only(self):
        form = ImportacaoLoteForm(fonte_locked='prodes')
        self.assertIn('arquivos', form.fields)
        self.assertNotIn('arquivo_lote', form.fields)

    def test_delete_is_not_mixed_into_import_form(self):
        form = ImportacaoLoteForm(fonte_locked='prodes')
        self.assertNotIn('limpar_antes_importar', form.fields)
