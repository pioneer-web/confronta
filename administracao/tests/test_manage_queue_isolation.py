from django.test import TestCase

from administracao.constants import FonteDados
from administracao.models import ItemLoteImportacao, LoteImportacao, User
from administracao.services.batch import claim_next_item


class ManageQueueIsolationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            email="fila@test.local", password="SenhaForte123!"
        )
        self.lote = LoteImportacao.objects.create(
            fonte=FonteDados.PRODES,
            nome_arquivo_original="teste.zip",
            hash_sha256="0" * 64,
            tamanho_bytes=1,
            administrador=self.user,
            status=LoteImportacao.Status.PROCESSANDO,
        )

    def _item(self, status, name):
        return ItemLoteImportacao.objects.create(
            lote=self.lote,
            caminho_relativo=name,
            nome_arquivo=name,
            status=status,
            etapa="Aguardando na fila",
        )

    def test_manage_worker_claims_only_manage_queue_status(self):
        legacy = self._item(ItemLoteImportacao.Status.PENDENTE, "legado.zip")
        manage = self._item(ItemLoteImportacao.Status.AGUARDANDO_FILA, "manage.zip")

        claimed = claim_next_item()

        self.assertEqual(claimed.pk, manage.pk)
        legacy.refresh_from_db()
        manage.refresh_from_db()
        self.assertEqual(legacy.status, ItemLoteImportacao.Status.PENDENTE)
        self.assertEqual(manage.status, ItemLoteImportacao.Status.PROCESSANDO)

    def test_queue_label_is_explicit(self):
        item = self._item(ItemLoteImportacao.Status.AGUARDANDO_FILA, "fila.zip")
        self.assertEqual(item.get_status_display(), "Aguardando na fila")
