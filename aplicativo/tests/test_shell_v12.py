from django.test import TestCase
from django.urls import reverse

from administracao.models import User
from aplicativo.models import PerfilCliente


class ShellInternoV12Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='cliente.shell@test.local',
            password='SenhaForte123!',
            first_name='Cliente Shell',
        )
        self.perfil = PerfilCliente.objects.create(
            usuario=self.user,
            plano=PerfilCliente.Plano.BASICO,
            telefone='11999999999',
            empresa='Fazenda Teste',
        )
        self.client.force_login(self.user)

    def test_shell_exibe_busca_ajuda_alertas_e_menu_usuario(self):
        response = self.client.get(reverse('aplicativo:inicio'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nova consulta')
        self.assertContains(response, 'Buscar CAR (ex.: PE-1234567...)')
        self.assertContains(response, 'Upload KML / KMZ')
        self.assertContains(response, 'Latitude / Longitude')
        self.assertContains(response, 'Central de ajuda')
        self.assertContains(response, 'Minha conta')
        self.assertContains(response, 'Meu plano')
        self.assertContains(response, 'id="map"', html=False)

    def test_ajuda_exibe_instrucoes_de_uso(self):
        response = self.client.get(reverse('aplicativo:ajuda'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Instruções básicas')
        self.assertContains(response, 'Consultar um CAR')
        self.assertContains(response, 'Camadas e glebas')
        self.assertNotContains(response, 'Ícones do menu superior')

    def test_conta_atualiza_dados_basicos_sem_alterar_plano(self):
        response = self.client.post(reverse('aplicativo:conta'), {
            'nome': 'Cliente Atualizado',
            'email': 'cliente.atualizado@test.local',
            'telefone': '11988887777',
            'empresa': 'Empresa Atualizada',
        })
        self.assertRedirects(response, reverse('aplicativo:conta'))

        self.user.refresh_from_db()
        self.perfil.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Cliente Atualizado')
        self.assertEqual(self.user.email, 'cliente.atualizado@test.local')
        self.assertEqual(self.perfil.telefone, '11988887777')
        self.assertEqual(self.perfil.empresa, 'Empresa Atualizada')
        self.assertEqual(self.perfil.plano, PerfilCliente.Plano.BASICO)
