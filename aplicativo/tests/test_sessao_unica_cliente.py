from django.test import Client, TestCase
from django.urls import reverse

from administracao.models import User
from aplicativo.models import PerfilCliente


class SessaoUnicaClienteTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email='sessao@test.local',
            password='SenhaForte123!',
        )
        self.perfil = PerfilCliente.objects.create(
            usuario=self.user,
            plano=PerfilCliente.Plano.BASICO,
        )

    def login_cliente(self, client):
        response = client.post(
            reverse('aplicativo:login'),
            {
                'email': 'sessao@test.local',
                'password': 'SenhaForte123!',
            },
        )
        self.assertEqual(response.status_code, 302)

    def test_segundo_login_invalida_primeiro(self):
        navegador_a = Client()
        navegador_b = Client()

        self.login_cliente(navegador_a)

        self.perfil.refresh_from_db()
        token_a = self.perfil.token_sessao_ativa
        self.assertTrue(token_a)

        self.login_cliente(navegador_b)

        self.perfil.refresh_from_db()
        token_b = self.perfil.token_sessao_ativa

        self.assertTrue(token_b)
        self.assertNotEqual(token_a, token_b)

        # O navegador antigo tenta continuar usando a conta.
        resposta_a = navegador_a.get(reverse('aplicativo:inicio'))

        self.assertEqual(resposta_a.status_code, 302)
        self.assertEqual(
            resposta_a.url,
            reverse('aplicativo:login'),
        )
        self.assertNotIn('_auth_user_id', navegador_a.session)

        # A sessão mais recente continua autenticada.
        navegador_b.get(reverse('aplicativo:inicio'))

        self.assertEqual(
            navegador_b.session.get('_auth_user_id'),
            str(self.user.pk),
        )

    def test_logout_limpa_sessao_ativa(self):
        navegador = Client()
        self.login_cliente(navegador)

        self.perfil.refresh_from_db()
        self.assertTrue(self.perfil.token_sessao_ativa)

        navegador.post(reverse('aplicativo:logout'))

        self.perfil.refresh_from_db()

        self.assertEqual(self.perfil.token_sessao_ativa, '')
        self.assertIsNone(self.perfil.sessao_ativa_em)

    def test_superadmin_nao_possui_limite_de_sessao(self):
        admin = User.objects.create_superuser(
            email='super-sessao@test.local',
            password='SenhaForte123!',
        )

        navegador_a = Client()
        navegador_b = Client()

        navegador_a.force_login(admin)
        navegador_b.force_login(admin)

        navegador_a.get(reverse('aplicativo:inicio'))
        navegador_b.get(reverse('aplicativo:inicio'))

        self.assertEqual(
            navegador_a.session.get('_auth_user_id'),
            str(admin.pk),
        )
        self.assertEqual(
            navegador_b.session.get('_auth_user_id'),
            str(admin.pk),
        )
