from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from administracao.forms import ImportacaoLoteForm, UploadBaseForm
from administracao.services.batch import _allowed_input_extensions


class SicarGpkgUploadContractTests(SimpleTestCase):
    def test_sicar_accepts_zip_and_gpkg(self):
        self.assertEqual(_allowed_input_extensions('sicar'), {'.zip', '.gpkg'})
        form = ImportacaoLoteForm(fonte_locked='sicar')
        self.assertEqual(form.fields['arquivos'].widget.attrs.get('accept'), '.zip,.gpkg')

    def test_other_sources_remain_zip_only(self):
        self.assertEqual(_allowed_input_extensions('prodes'), {'.zip'})
        form = ImportacaoLoteForm(fonte_locked='prodes')
        self.assertEqual(form.fields['arquivos'].widget.attrs.get('accept'), '.zip')

    def test_single_sicar_form_accepts_gpkg_extension(self):
        upload = SimpleUploadedFile('AREA_IMOVEL.gpkg', b'SQLite format 3\x00' + b'0' * 128)
        form = UploadBaseForm(files={'arquivo': upload}, source_slug='sicar')
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_prodes_rejects_direct_gpkg(self):
        upload = SimpleUploadedFile('prodes.gpkg', b'SQLite format 3\x00' + b'0' * 128)
        form = UploadBaseForm(files={'arquivo': upload}, source_slug='prodes')
        self.assertFalse(form.is_valid())
