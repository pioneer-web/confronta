from decimal import Decimal

from django.test import Client as DjangoClient, TestCase, override_settings
from django.urls import reverse

from administracao.models import Auditoria, User
from administracao.forms import ClienteNovoAdminForm
from aplicativo.models import PerfilCliente, PlanoComercial
from allauth.socialaccount.models import SocialAccount
from billing.models import AsaasCheckout, AssinaturaAsaas


@override_settings(ROOT_URLCONF='administracao.tests.urls')
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

    def _criar_cliente(self, email='editar.cliente@test.local', **kwargs):
        user = User.objects.create_user(email=email, password='SenhaAntiga123!x', **{
            key: value for key, value in kwargs.items() if key in {'first_name', 'last_name'}
        })
        perfil = PerfilCliente.objects.create(
            usuario=user, plano=self.plano.nivel_acesso, plano_comercial=self.plano,
            telefone='81999990000', **{key: value for key, value in kwargs.items() if key not in {'first_name', 'last_name'}},
        )
        return user, perfil

    def _dados_edicao(self, perfil, **extra):
        dados = {
            'nome': perfil.usuario.first_name or 'Cliente',
            'sobrenome': perfil.usuario.last_name or 'Teste',
            'email': perfil.usuario.email,
            'telefone': perfil.telefone,
            'plano_comercial': str(self.plano.pk),
            'inicio_acesso': '', 'fim_acesso': '', 'ativo': 'on',
            'observacoes_admin': '', 'nova_senha1': '', 'nova_senha2': '',
        }
        dados.update(extra)
        return dados

    def _post_edicao(self, perfil, **extra):
        return self.client.post(
            reverse('administracao:cliente_editar', args=[perfil.pk]),
            self._dados_edicao(perfil, **extra),
        )

    def test_admin_total_edita_nome_e_email(self):
        user, perfil = self._criar_cliente()
        self._post_edicao(perfil, nome='Nome novo', email='novo.email@test.local')
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Nome novo')
        self.assertEqual(user.email, 'novo.email@test.local')

    def test_email_duplicado_e_rejeitado_na_edicao(self):
        _, perfil = self._criar_cliente()
        self._criar_cliente(email='ocupado@test.local')
        response = self._post_edicao(perfil, email='OCUPADO@test.local')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Já existe uma conta cadastrada com este e-mail.')

    def test_senha_permanece_igual_quando_campos_vazios(self):
        user, perfil = self._criar_cliente()
        encoded = user.password
        self._post_edicao(perfil)
        user.refresh_from_db()
        self.assertEqual(user.password, encoded)

    def test_nova_senha_e_salva_com_set_password(self):
        user, perfil = self._criar_cliente()
        self._post_edicao(perfil, nova_senha1='NovaSenhaForte123!x', nova_senha2='NovaSenhaForte123!x')
        user.refresh_from_db()
        self.assertTrue(user.check_password('NovaSenhaForte123!x'))

    def test_html_nao_exibe_senha_atual(self):
        user, perfil = self._criar_cliente()
        response = self.client.get(reverse('administracao:cliente_editar', args=[perfil.pk]))
        self.assertNotContains(response, 'SenhaAntiga123!x')
        self.assertNotContains(response, user.password)

    def test_conta_google_recebe_senha_local_sem_perder_vinculo(self):
        user, perfil = self._criar_cliente()
        user.set_unusable_password()
        user.save(update_fields=['password'])
        social = SocialAccount.objects.create(user=user, provider='google', uid='google-local-test')
        response = self._post_edicao(perfil, nova_senha1='SenhaGoogleNova123!x', nova_senha2='SenhaGoogleNova123!x')
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.check_password('SenhaGoogleNova123!x'))
        self.assertTrue(SocialAccount.objects.filter(pk=social.pk, user=user).exists())

    def test_cliente_sem_historico_pode_ser_excluido_com_auditoria(self):
        user, perfil = self._criar_cliente()
        perfil_id, user_id = perfil.pk, user.pk
        response = self.client.post(reverse('administracao:cliente_excluir', args=[perfil_id]), {'confirmar_exclusao': 'sim'})
        self.assertRedirects(response, reverse('administracao:clientes'))
        self.assertFalse(PerfilCliente.objects.filter(pk=perfil_id).exists())
        self.assertFalse(User.objects.filter(pk=user_id).exists())
        auditoria = Auditoria.objects.get(acao='CLIENTE_EXCLUIDO', identificador=str(perfil_id))
        self.assertEqual(auditoria.detalhes['user_id'], user_id)

    def test_cliente_com_checkout_ou_assinatura_nao_pode_ser_excluido(self):
        user, perfil = self._criar_cliente()
        AsaasCheckout.objects.create(
            usuario=user, perfil=perfil, plano=self.plano,
            ciclo=AsaasCheckout.Ciclo.MONTHLY, valor=Decimal('10.00'),
        )
        response = self.client.post(reverse('administracao:cliente_excluir', args=[perfil.pk]), {'confirmar_exclusao': 'sim'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Este cliente possui histórico financeiro e não pode ser excluído.')
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_cliente_com_assinatura_nao_pode_ser_excluido(self):
        user, perfil = self._criar_cliente()
        AssinaturaAsaas.objects.create(
            perfil=perfil, plano=self.plano, ciclo=AsaasCheckout.Ciclo.MONTHLY,
            valor=Decimal('10.00'), asaas_subscription_id='sub-admin-test',
        )
        response = self.client.post(reverse('administracao:cliente_excluir', args=[perfil.pk]), {'confirmar_exclusao': 'sim'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_admin_junior_nao_pode_excluir_cliente(self):
        user, perfil = self._criar_cliente()
        junior = User.objects.create_user(email='junior.delete@test.local', password='SenhaForte123!', role=User.Role.ADMIN_JUNIOR, is_staff=True)
        self.client.force_login(junior)
        response = self.client.post(reverse('administracao:cliente_excluir', args=[perfil.pk]), {'confirmar_exclusao': 'sim'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_get_nao_exclui_cliente(self):
        user, perfil = self._criar_cliente()
        response = self.client.get(reverse('administracao:cliente_excluir', args=[perfil.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    @override_settings(MIDDLEWARE=['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware'])
    def test_post_exclusao_exige_csrf(self):
        user, perfil = self._criar_cliente()
        client = DjangoClient(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post(reverse('administracao:cliente_excluir', args=[perfil.pk]), {'confirmar_exclusao': 'sim'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_ativar_desativar_cliente_continua_funcionando(self):
        _, perfil = self._criar_cliente()
        response = self.client.post(reverse('administracao:cliente_alternar', args=[perfil.pk]))
        perfil.refresh_from_db()
        self.assertRedirects(response, reverse('administracao:clientes'))
        self.assertFalse(perfil.ativo)

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
