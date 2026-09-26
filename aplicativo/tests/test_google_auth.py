from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.conf import settings

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import EmailAddress, SocialAccount, SocialLogin

from administracao.models import User
from aplicativo.adapters import ClienteAccountAdapter, ClienteSocialAccountAdapter
from aplicativo.models import PerfilCliente, PlanoComercial
from aplicativo.permissions import SESSION_LOGOUT_LOCAL
from aplicativo.session_control import SESSION_CLIENT_TOKEN


class GoogleAuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()

    def _plano(self):
        plano, _ = PlanoComercial.objects.get_or_create(
            slug='confronta',
            defaults={'nome': 'CONFRONTA', 'nivel_acesso': 'TOTAL', 'ativo': True},
        )
        return plano

    def _request(self):
        request = self.factory.get('/accounts/google/login/callback/')
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def _sociallogin(self, email, *, verified=True, uid='google-id'):
        return SocialLogin(
            account=SocialAccount(
                provider='google', uid=uid,
                extra_data={
                    'email': email,
                    'given_name': 'Maria',
                    'family_name': 'Silva',
                    'email_verified': verified,
                },
            ),
            email_addresses=[EmailAddress(email=email, verified=verified, primary=True)],
        )

    def _google_login_response(self, request, user, *, signup):
        request._confronta_login_google = True
        return ClienteAccountAdapter().post_login(
            request,
            user,
            email_verification='none',
            signal_kwargs={},
            email=None,
            signup=signup,
            redirect_url='https://attacker.example/redirect',
        )

    @override_settings(GOOGLE_OAUTH_ENABLED=False)
    def test_google_button_hidden_without_credentials(self):
        response = self.client.get('/mapa/login/')
        self.assertNotContains(response, 'Continuar com Google')
        self._plano()
        response = self.client.get('/mapa/cadastro/mensal/')
        self.assertNotContains(response, 'Continuar com Google')

    @override_settings(GOOGLE_OAUTH_ENABLED=True)
    def test_google_button_shown_when_enabled(self):
        response = self.client.get('/mapa/login/')
        self.assertContains(response, 'Continuar com Google')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self._plano()
        response = self.client.get('/mapa/cadastro/mensal/')
        self.assertContains(response, 'Continuar com Google')

    def test_google_callback_url_is_registered(self):
        self.assertEqual(
            reverse('google_callback'),
            '/accounts/google/login/callback/',
        )

    def test_new_verified_google_user_gets_user_and_sem_plano_profile(self):
        plano = self._plano()
        request = self._request()
        sociallogin = self._sociallogin('maria@example.com')
        adapter = ClienteSocialAccountAdapter()

        adapter.pre_social_login(request, sociallogin)
        sociallogin.user = adapter.new_user(request, sociallogin)
        sociallogin.user = adapter.populate_user(
            request, sociallogin, {'email': 'maria@example.com'},
        )
        user = adapter.save_user(request, sociallogin)

        self.assertEqual(User.objects.filter(email='maria@example.com').count(), 1)
        self.assertEqual(user.first_name, 'Maria')
        self.assertEqual(user.last_name, 'Silva')
        self.assertFalse(user.has_usable_password())
        perfil = user.perfil_cliente
        self.assertEqual(perfil.telefone, '')
        self.assertEqual(perfil.plano, PerfilCliente.Plano.SEM_PLANO)
        self.assertTrue(perfil.ativo)
        self.assertFalse(perfil.renovacao_automatica)
        self.assertEqual(perfil.plano_desejado, plano.nivel_acesso)
        self.assertEqual(perfil.plano_desejado_comercial, plano)
        response = self._google_login_response(request, user, signup=True)
        self.assertEqual(response['Location'], '/mapa/')
        self.assertEqual(list(request._messages), [])
        self.assertEqual(settings.LOGIN_REDIRECT_URL, 'administracao:dashboard')

    def test_existing_client_is_connected_without_changing_password_or_profile(self):
        senha_original = 'CurrentPassword123!'
        user = User.objects.create_user(email='client@example.com', password=senha_original)
        perfil = PerfilCliente.objects.create(usuario=user, plano=PerfilCliente.Plano.BASICO)
        perfil_original = {
            'plano': perfil.plano,
            'plano_desejado': perfil.plano_desejado,
            'plano_comercial_id': perfil.plano_comercial_id,
            'plano_desejado_comercial_id': perfil.plano_desejado_comercial_id,
            'renovacao_automatica': perfil.renovacao_automatica,
        }
        request = self._request()
        sociallogin = self._sociallogin('client@example.com')

        ClienteSocialAccountAdapter().pre_social_login(request, sociallogin)
        user.refresh_from_db()
        perfil.refresh_from_db()

        self.assertEqual(User.objects.filter(email='client@example.com').count(), 1)
        self.assertTrue(SocialAccount.objects.filter(user=user, provider='google').exists())
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password(senha_original))
        self.assertEqual(
            {key: getattr(perfil, key) for key in perfil_original},
            perfil_original,
        )

        request.session[SESSION_LOGOUT_LOCAL] = True
        response = self._google_login_response(request, user, signup=False)
        self.assertEqual(response['Location'], '/mapa/')
        user.perfil_cliente.refresh_from_db()
        self.assertTrue(user.perfil_cliente.token_sessao_ativa)
        self.assertEqual(
            request.session[SESSION_CLIENT_TOKEN],
            user.perfil_cliente.token_sessao_ativa,
        )
        self.assertNotIn(SESSION_LOGOUT_LOCAL, request.session)

    def test_unverified_google_email_is_rejected(self):
        request = self._request()
        sociallogin = self._sociallogin('unverified@example.com', verified=False)
        with self.assertRaises(ImmediateHttpResponse):
            ClienteSocialAccountAdapter().pre_social_login(request, sociallogin)
        self.assertFalse(User.objects.filter(email='unverified@example.com').exists())

    def test_admin_accounts_cannot_use_google(self):
        cases = (
            {'email': 'staff@example.com', 'is_staff': True},
            {'email': 'super@example.com', 'is_superuser': True, 'is_staff': True},
            {'email': 'total@example.com', 'role': User.Role.ADMIN_TOTAL},
            {'email': 'junior@example.com', 'role': User.Role.ADMIN_JUNIOR},
        )
        adapter = ClienteSocialAccountAdapter()
        for fields in cases:
            with self.subTest(email=fields['email']):
                user = User.objects.create_user(password='AValidPassword123!', **fields)
                request = self._request()
                sociallogin = self._sociallogin(user.email, uid=user.email)
                with self.assertRaises(ImmediateHttpResponse):
                    adapter.pre_social_login(request, sociallogin)
                self.assertEqual([str(message) for message in request._messages], [adapter.erro_conta_admin])
                user.refresh_from_db()
                self.assertFalse(hasattr(user, 'perfil_cliente'))
                self.assertEqual(User.objects.filter(email=user.email).count(), 1)

    def test_traditional_login_continues_to_work(self):
        user = User.objects.create_user(email='password@example.com', password='SenhaForte123!')
        PerfilCliente.objects.create(usuario=user, plano=PerfilCliente.Plano.SEM_PLANO)
        response = self.client.post('/mapa/login/', {
            'email': user.email, 'password': 'SenhaForte123!',
        })
        self.assertRedirects(response, '/mapa/', fetch_redirect_response=False)
        self.assertEqual(str(self.client.session[SESSION_KEY]), str(user.pk))

    def test_traditional_registration_continues_to_work(self):
        self._plano()
        response = self.client.post('/mapa/cadastro/mensal/', {
            'nome': 'Cliente de Teste',
            'email': 'tradicional@example.com',
            'telefone': '(81) 99999-9999',
            'password1': 'SenhaForte123!',
            'password2': 'SenhaForte123!',
        })
        self.assertRedirects(response, '/mapa/cadastro/concluido/', fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(email='tradicional@example.com').exists())
