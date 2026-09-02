from django.test import TestCase
from django.urls import reverse

from administracao.models import User
from administracao.forms import ClienteNovoAdminForm
from aplicativo.models import PerfilCliente, PlanoComercial


class ClienteAdministracaoTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin.total@test.local',
            password='SenhaForte123!',
            role=User.Role.ADMIN_TOTAL,
            is_staff=True,
        )
        self.plano = PlanoComercial.objects.create(
            nome='Plano Contratado',
            slug='plano-contratado',
            nivel_acesso=PerfilCliente.Plano.BASICO,
            ativo=True,
        )
        self.client.force_login(self.admin)

    def _dados(self, **extra):
        dados = {
            'nome': 'Cliente',
            'sobrenome': 'Administrativo',
            'email': 'cliente.admin@test.local',
            'telefone': '(11) 97777-6666',
            'password1': 'SenhaForte123!x',
            'password2': 'SenhaForte123!x',
            'plano_comercial': str(self.plano.pk),
            'inicio_acesso': '',
            'fim_acesso': '',
            'ativo': 'on',
            'observacoes_admin': 'Contratação confirmada.',
        }
        dados.update(extra)
        return dados

    def test_admin_total_cria_cliente_somente_com_plano_ativo(self):
        response = self.client.post(reverse('administracao:cliente_novo'), self._dados())
        self.assertRedirects(response, reverse('administracao:clientes'))
        perfil = PerfilCliente.objects.select_related('usuario', 'plano_comercial').get(usuario__email='cliente.admin@test.local')
        self.assertEqual(perfil.plano_comercial, self.plano)
        self.assertEqual(perfil.plano, PerfilCliente.Plano.BASICO)
        self.assertTrue(perfil.ativo)

    def test_admin_nao_cria_cliente_sem_plano(self):
        response = self.client.post(reverse('administracao:cliente_novo'), self._dados(plano_comercial=''))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Este campo é obrigatório')
        self.assertFalse(User.objects.filter(email='cliente.admin@test.local').exists())

    def test_admin_nao_cria_email_duplicado(self):
        existente = User.objects.create_user(email='duplicado@test.local', password='SenhaForte123!')
        PerfilCliente.objects.create(usuario=existente, plano=self.plano.nivel_acesso, plano_comercial=self.plano)
        response = self.client.post(reverse('administracao:cliente_novo'), self._dados(email='DUPLICADO@test.local'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Já existe uma conta cadastrada com este e-mail.')
        self.assertEqual(User.objects.filter(email='duplicado@test.local').count(), 1)
    def test_formulario_administrativo_nao_exibe_cpf_nem_empresa(self):
        form = ClienteNovoAdminForm()
        self.assertNotIn('cpf', form.fields)
        self.assertNotIn('empresa', form.fields)
        response = self.client.get(reverse('administracao:cliente_novo'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="cpf"')
        self.assertNotContains(response, 'name="empresa"')



    def test_admin_total_pode_redefinir_senha_do_cliente(self):
        user = User.objects.create_user(email='senha.cliente@test.local', password='SenhaAntiga123!x')
        perfil = PerfilCliente.objects.create(
            usuario=user,
            telefone='81999990000',
            plano=self.plano.nivel_acesso,
            plano_comercial=self.plano,
            ativo=True,
        )
        response = self.client.post(
            reverse('administracao:cliente_editar', args=[perfil.pk]),
            {
                'nome': 'Cliente',
                'sobrenome': 'Senha',
                'email': 'senha.cliente@test.local',
                'telefone': '81999990000',
                'plano_comercial': str(self.plano.pk),
                'inicio_acesso': '',
                'fim_acesso': '',
                'ativo': 'on',
                'observacoes_admin': '',
                'nova_senha1': 'NovaSenhaForte123!x',
                'nova_senha2': 'NovaSenhaForte123!x',
            },
        )
        self.assertRedirects(response, reverse('administracao:clientes'))
        user.refresh_from_db()
        self.assertTrue(user.check_password('NovaSenhaForte123!x'))
        self.assertFalse(user.check_password('SenhaAntiga123!x'))

    def test_admin_junior_nao_pode_editar_cliente(self):
        junior = User.objects.create_user(
            email='admin.junior@test.local',
            password='SenhaForte123!',
            role=User.Role.ADMIN_JUNIOR,
            is_staff=True,
        )
        user = User.objects.create_user(email='cliente.bloqueado@test.local', password='SenhaForte123!x')
        perfil = PerfilCliente.objects.create(
            usuario=user,
            plano=self.plano.nivel_acesso,
            plano_comercial=self.plano,
        )
        self.client.force_login(junior)
        response = self.client.get(reverse('administracao:cliente_editar', args=[perfil.pk]))
        self.assertEqual(response.status_code, 403)

    def test_edicao_get_carrega_dados_existentes_mesmo_sem_assinatura(self):
        user = User.objects.create_user(
            email='prefill.cliente@test.local',
            password='SenhaForte123!x',
            first_name='Maria',
            last_name='Silva',
        )
        perfil = PerfilCliente.objects.create(
            usuario=user,
            telefone='81999998888',
            plano=PerfilCliente.Plano.SEM_PLANO,
            plano_comercial=None,
            ativo=True,
            renovacao_automatica=False,
            observacoes_admin='Cliente aguardando pagamento.',
        )
        response = self.client.get(reverse('administracao:cliente_editar', args=[perfil.pk]))
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form['nome'].value(), 'Maria')
        self.assertEqual(form['sobrenome'].value(), 'Silva')
        self.assertEqual(form['email'].value(), 'prefill.cliente@test.local')
        self.assertEqual(form['telefone'].value(), '81999998888')
        self.assertEqual(form['observacoes_admin'].value(), 'Cliente aguardando pagamento.')
        self.assertTrue(form['ativo'].value())

