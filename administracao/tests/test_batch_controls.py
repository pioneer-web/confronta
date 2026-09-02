from django.test import TestCase

from administracao.constants import FonteDados
from administracao.models import ItemLoteImportacao, LoteImportacao, User
from administracao.services.batch import request_batch_interruption


class BatchControlTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='admin@example.com', password='x')

    def test_interrupt_waiting_batch_stops_items_without_marking_failure(self):
        lote = LoteImportacao.objects.create(
            fonte=FonteDados.SICOR,
            nome_arquivo_original='1 arquivo(s) — envio sequencial',
            hash_sha256='0' * 64,
            tamanho_bytes=10,
            administrador=self.user,
            status=LoteImportacao.Status.PROCESSANDO,
            resultado={'modo': 'UPLOAD_SEQUENCIAL'},
        )
        item = ItemLoteImportacao.objects.create(
            lote=lote,
            caminho_relativo='item_0001/teste.gz',
            nome_arquivo='teste.gz',
            status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
        )
        lote = request_batch_interruption(lote.pk, self.user)
        item.refresh_from_db()
        self.assertEqual(lote.status, LoteImportacao.Status.INTERROMPIDO)
        self.assertEqual(item.status, ItemLoteImportacao.Status.INTERROMPIDO)
        self.assertTrue(lote.resultado['interrupcao_solicitada'])

    def test_remove_interrompendo_lot_from_queue_without_deleting_published_data(self):
        from administracao.services.batch import delete_batch_record

        lote = LoteImportacao.objects.create(
            fonte=FonteDados.SICOR,
            nome_arquivo_original='1 arquivo(s) — envio sequencial',
            hash_sha256='1' * 64,
            tamanho_bytes=10,
            administrador=self.user,
            status=LoteImportacao.Status.INTERROMPENDO,
            resultado={'modo': 'UPLOAD_SEQUENCIAL', 'interrupcao_solicitada': True},
        )
        item = ItemLoteImportacao.objects.create(
            lote=lote,
            caminho_relativo='item_0001/sicor.gz',
            nome_arquivo='sicor.gz',
            status=ItemLoteImportacao.Status.PROCESSANDO,
        )

        delete_batch_record(lote.pk, self.user)

        lote.refresh_from_db()
        item.refresh_from_db()
        self.assertTrue(lote.oculto_painel)
        self.assertIsNotNone(lote.removido_painel_em)
        self.assertEqual(lote.status, LoteImportacao.Status.INTERROMPENDO)
        self.assertEqual(item.status, ItemLoteImportacao.Status.PROCESSANDO)
        self.assertTrue(lote.resultado['removido_da_fila'])

    def test_remove_waiting_lot_marks_item_interrupted_and_hides_queue_entry(self):
        from administracao.services.batch import delete_batch_record

        lote = LoteImportacao.objects.create(
            fonte=FonteDados.SICOR,
            nome_arquivo_original='1 arquivo(s) — envio sequencial',
            hash_sha256='2' * 64,
            tamanho_bytes=10,
            administrador=self.user,
            status=LoteImportacao.Status.PROCESSANDO,
            resultado={'modo': 'UPLOAD_SEQUENCIAL'},
        )
        item = ItemLoteImportacao.objects.create(
            lote=lote,
            caminho_relativo='item_0001/sicor.gz',
            nome_arquivo='sicor.gz',
            status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
        )

        delete_batch_record(lote.pk, self.user)

        lote.refresh_from_db()
        item.refresh_from_db()
        self.assertTrue(lote.oculto_painel)
        self.assertEqual(lote.status, LoteImportacao.Status.INTERROMPIDO)
        self.assertEqual(item.status, ItemLoteImportacao.Status.INTERROMPIDO)
