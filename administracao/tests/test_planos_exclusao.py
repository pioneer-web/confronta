from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from aplicativo.models import PerfilCliente, PlanoComercial


class PlanoExclusaoTests(TestCase):
    def setUp(self):
        User = get_user_model()

        self.admin = User.objects.create_superuser(
            email='admin-planos@confronta.test',
            password='SenhaSegura123!',
        )

        self.client.force_login(self.admin)

    def novo_plano(self, slug):
        return PlanoComercial.objects.create(
            nome=f'Plano {slug}',
            slug=slug,
            nivel_acesso=PlanoComercial.NivelAcesso.BASICO,
            preco_mensal=Decimal('10.00'),
            preco_anual=Decimal('100.00'),
            ativo=False,
        )

    def test_exclui_plano_sem_vinculos(self):
        plano = self.novo_plano('temporario')

        response = self.client.post(
            reverse(
                'administracao:plano_excluir',
                args=[plano.pk],
            )
        )

        self.assertRedirects(
            response,
            reverse('administracao:planos'),
        )

        self.assertFalse(
            PlanoComercial.objects.filter(
                pk=plano.pk
            ).exists()
        )

    def test_nao_exclui_plano_vinculado_a_cliente(self):
        User = get_user_model()

        plano = self.novo_plano('vinculado')

        usuario = User.objects.create_user(
            email='cliente-planos@confronta.test',
            password='SenhaSegura123!',
        )

        PerfilCliente.objects.create(
            usuario=usuario,
            plano_comercial=plano,
        )

        response = self.client.post(
            reverse(
                'administracao:plano_excluir',
                args=[plano.pk],
            )
        )

        self.assertRedirects(
            response,
            reverse('administracao:planos'),
        )

        self.assertTrue(
            PlanoComercial.objects.filter(
                pk=plano.pk
            ).exists()
        )
