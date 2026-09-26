import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from administracao.forms import UploadBaseForm
from administracao.models import ItemLoteImportacao, LoteImportacao, User
from administracao.services import batch_upload
from administracao.services.batch_creation import (
    create_batch_from_uploads,
    create_sequential_batch,
)
from administracao.services.batch_sequential import (
    append_sequential_upload,
    reject_empty_sequential_batch,
)


class BatchResourceLimitTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.working = root / 'working'
        self.recovery = root / 'recovery'
        self.working.mkdir()
        self.recovery.mkdir()
        settings_patch = override_settings(
            BATCH_DIR=self.working / 'files',
            BATCH_STORAGE_DIR=self.working,
            BATCH_RECOVERY_DIR=self.recovery,
            IMPORT_INBOX_DIR=root / 'inbox',
            MAX_UPLOAD_SIZE_BYTES=8,
            MAX_BATCH_UPLOAD_TOTAL_BYTES=10,
            MAX_BATCH_UPLOAD_FILES=2,
            MIN_FREE_DISK_BYTES=5,
            MAX_ZIP_ENTRIES=10,
            MAX_ZIP_EXPANSION_RATIO=200,
            MAX_ZIP_UNCOMPRESSED_BYTES=1024,
        )
        settings_patch.enable()
        self.addCleanup(settings_patch.disable)
        self.user = User.objects.create_user(email='manage@test.local', password='test-password')
        self.disk_patch = patch.object(
            batch_upload.shutil,
            'disk_usage',
            return_value=SimpleNamespace(free=10_000),
        )
        self.disk_patch.start()
        self.addCleanup(self.disk_patch.stop)

    @staticmethod
    def upload(name, content):
        return SimpleUploadedFile(name, content)

    def test_arquivo_individual_abaixo_do_limite_e_aceito(self):
        arquivo = self.upload('dados.csv', b'12345678')
        self.assertEqual(batch_upload.validate_upload_limits([arquivo]), 8)

    def test_arquivo_individual_acima_do_limite_e_rejeitado(self):
        arquivo = self.upload('dados.csv', b'123456789')
        with self.assertRaisesMessage(batch_upload.BatchUploadLimitError, batch_upload.MAX_FILE_MESSAGE):
            batch_upload.validate_upload_limits([arquivo])

    def test_form_de_importacao_individual_bloqueia_antes_do_pipeline(self):
        form = UploadBaseForm(
            files={'arquivo': self.upload('dados.csv', b'123456789')},
            source_slug='sicor',
            dataset_slug='sicor-propriedades',
        )
        self.assertFalse(form.is_valid())
        self.assertIn(batch_upload.MAX_FILE_MESSAGE, str(form.errors))

    def test_lote_abaixo_do_total_e_da_quantidade_e_aceito(self):
        lote = create_batch_from_uploads(
            [self.upload('propostas.csv', b'1234'), self.upload('operacoes.csv', b'5678')],
            'sicor',
            self.user,
        )
        self.assertEqual(lote.status, LoteImportacao.Status.PROCESSANDO)
        self.assertEqual(lote.tamanho_bytes, 8)
        self.assertEqual(lote.itens.count(), 2)

    def test_lote_acima_do_agregado_nao_cria_registro_nem_arquivo(self):
        with self.assertRaisesMessage(batch_upload.BatchUploadLimitError, batch_upload.MAX_BATCH_TOTAL_MESSAGE):
            create_batch_from_uploads(
                [self.upload('um.csv', b'123456'), self.upload('dois.csv', b'12345')],
                'sicor',
                self.user,
            )
        self.assertEqual(LoteImportacao.objects.count(), 0)
        self.assertEqual(list(self.working.rglob('*')), [])

    def test_excesso_de_arquivos_e_rejeitado_antes_de_criar_o_lote(self):
        with self.assertRaisesMessage(batch_upload.BatchUploadLimitError, batch_upload.MAX_BATCH_FILES_MESSAGE):
            create_batch_from_uploads(
                [self.upload(f'{n}.csv', b'a') for n in range(3)], 'sicor', self.user
            )
        self.assertEqual(LoteImportacao.objects.count(), 0)
        with self.assertRaisesMessage(batch_upload.BatchUploadLimitError, batch_upload.MAX_BATCH_FILES_MESSAGE):
            create_sequential_batch('sicor', self.user, 3)
        self.assertEqual(LoteImportacao.objects.count(), 0)

    def test_upload_sequencial_contabiliza_o_total_acumulado_sem_orfaos(self):
        lote = create_sequential_batch('sicor', self.user, 2)
        primeiro = append_sequential_upload(
            lote.pk, self.upload('propostas.csv', b'1234'), self.user
        )
        root = Path(lote.extracted_path)
        (root / primeiro.caminho_relativo).unlink()
        (self.recovery / f'lote_{lote.pk}' / primeiro.caminho_relativo).unlink(missing_ok=True)
        primeiro.status = ItemLoteImportacao.Status.CONCLUIDO
        primeiro.save(update_fields=['status'])

        with self.assertRaisesMessage(batch_upload.BatchUploadLimitError, batch_upload.MAX_BATCH_TOTAL_MESSAGE):
            append_sequential_upload(
                lote.pk, self.upload('operacoes.csv', b'1234567'), self.user, index=2
            )

        lote.refresh_from_db()
        self.assertEqual(lote.tamanho_bytes, 4)
        self.assertEqual(lote.itens.count(), 1)
        self.assertFalse((root / 'item_0002' / 'operacoes.csv').exists())
        self.assertFalse((self.recovery / f'lote_{lote.pk}' / 'item_0002' / 'operacoes.csv').exists())

    def test_primeiro_upload_sequencial_recusado_finaliza_lote_vazio_e_limpa_diretorio(self):
        lote = create_sequential_batch('sicor', self.user, 1)
        with self.assertRaises(batch_upload.BatchUploadLimitError):
            append_sequential_upload(
                lote.pk, self.upload('propostas.csv', b'123456789'), self.user
            )

        self.assertTrue(
            reject_empty_sequential_batch(
                lote.pk, self.user, batch_upload.MAX_FILE_MESSAGE
            )
        )
        lote.refresh_from_db()
        self.assertEqual(lote.status, LoteImportacao.Status.FALHOU)
        self.assertEqual(lote.motivo_falha, batch_upload.MAX_FILE_MESSAGE)
        self.assertFalse(Path(lote.extracted_path).exists())
        self.assertEqual(lote.itens.count(), 0)

    def test_reserva_de_disco_inclui_tamanho_do_novo_upload(self):
        arquivo = self.upload('dados.csv', b'1234')
        self.disk_patch.stop()
        with patch.object(batch_upload.shutil, 'disk_usage', return_value=SimpleNamespace(free=8)):
            with self.assertRaisesMessage(
                batch_upload.BatchUploadLimitError,
                batch_upload.INSUFFICIENT_SPACE_MESSAGE,
            ):
                batch_upload.validate_upload_limits([arquivo])

        self.disk_patch.start()

    def test_mensagem_de_espaco_nao_expoe_path_interno(self):
        arquivo = self.upload('dados.csv', b'1234')
        self.disk_patch.stop()
        with patch.object(batch_upload.shutil, 'disk_usage', return_value=SimpleNamespace(free=8)):
            with self.assertRaises(batch_upload.BatchUploadLimitError) as caught:
                batch_upload.validate_upload_limits([arquivo])
        self.assertEqual(str(caught.exception), batch_upload.INSUFFICIENT_SPACE_MESSAGE)
        self.assertNotIn(str(self.working), str(caught.exception))
        self.disk_patch.start()


class UploadProxyLimitContractTests(TestCase):
    def test_nginx_upload_locations_are_bounded_and_not_unlimited(self):
        root = Path(__file__).resolve().parents[2]
        for relative in ('deploy/nginx/manage.conf', 'deploy/nginx/confronta.conf'):
            content = (root / relative).read_text(encoding='utf-8')
            self.assertNotIn('client_max_body_size 0;', content)
            self.assertIn('client_max_body_size 2050m;', content)
