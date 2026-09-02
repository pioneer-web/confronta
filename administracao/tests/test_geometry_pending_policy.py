from django.test import TestCase

from administracao.constants import FonteDados
from administracao.models import Importacao, ItemLoteImportacao, LoteImportacao, User
from administracao.services.batch import update_batch_status


class GeometryPendingPolicyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            email='geometria@test.local', password='SenhaForte123!'
        )

    def test_geometry_pending_marks_batch_with_warnings_without_failing_item(self):
        lote = LoteImportacao.objects.create(
            fonte=FonteDados.SICAR,
            nome_arquivo_original='PE_APP.gpkg',
            hash_sha256='1' * 64,
            tamanho_bytes=1,
            administrador=self.user,
            status=LoteImportacao.Status.PROCESSANDO,
            resultado={'fase': 'IMPORTACAO'},
        )
        imp = Importacao.objects.create(
            fonte=FonteDados.SICAR,
            dataset_slug='sicar-app',
            dataset_label='APP',
            nome_arquivo_original='PE_APP.gpkg',
            hash_sha256='2' * 64,
            tamanho_bytes=1,
            administrador=self.user,
            status=Importacao.Status.CONCLUIDO,
            resultado={
                'reparo_geometrias': {
                    'detectadas': 10,
                    'reparaveis': 8,
                    'nao_reparaveis': 2,
                    'raw_preservada': True,
                }
            },
        )
        ItemLoteImportacao.objects.create(
            lote=lote,
            caminho_relativo='item_0001/PE_APP.gpkg',
            nome_arquivo='PE_APP.gpkg',
            uf='PE',
            dataset_slug='sicar-app',
            dataset_label='APP',
            status=ItemLoteImportacao.Status.CONCLUIDO,
            progresso=100,
            importacao=imp,
        )

        lote = update_batch_status(lote.pk)

        self.assertEqual(lote.status, LoteImportacao.Status.CONCLUIDO_COM_PENDENCIAS)
        self.assertEqual(lote.resultado.get('pendencias_geometria'), 2)
        self.assertEqual(lote.resultado.get('pendencias_relatorio'), 2)
