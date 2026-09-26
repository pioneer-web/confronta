from django.test import Client, TestCase, override_settings
from django.urls import reverse

from administracao.models import User
from aplicativo.models import LimiteSeguranca


@override_settings(
    ROOT_URLCONF='administracao.tests.urls',
    ADMIN_LOGIN_FAILURE_LIMIT=5,
    ADMIN_LOGIN_FAILURE_WINDOW_SECONDS=300,
    ADMIN_LOGIN_IDENTITY_FAILURE_LIMIT=8,
    ADMIN_LOGIN_IP_FAILURE_LIMIT=20,
    LOGIN_BLOCK_SECONDS=600,
    LOGIN_IDENTITY_BLOCK_SECONDS=600,
    LOGIN_IP_BLOCK_SECONDS=600,
    TRUST_PROXY_HEADERS=False,
)
class AdminLoginRateLimitTests(TestCase):
    url_name = 'administracao:login'
    password = 'NaoDeveAparecer123!'

    def setUp(self):
        self.url = reverse(self.url_name)

    def post_login(self, email, password='senha-incorreta', ip='203.0.113.10'):
        return self.client.post(
            self.url,
            {'email': email, 'password': password},
            REMOTE_ADDR=ip,
        )

    def create_admin(self, email, role):
        if role == 'SUPERUSER':
            return User.objects.create_superuser(email=email, password=self.password)
        return User.objects.create_user(email=email, password=self.password, role=role)

    def test_login_valido_preserva_admin_junior_total_e_superuser(self):
        roles = (
            ('junior@test.local', User.Role.ADMIN_JUNIOR),
            ('total@test.local', User.Role.ADMIN_TOTAL),
            ('super@test.local', 'SUPERUSER'),
        )
        for index, (email, role) in enumerate(roles):
            with self.subTest(role=role):
                user = self.create_admin(email, role)
                client = Client()
                response = client.post(
                    self.url,
                    {'email': email.upper(), 'password': self.password},
                    REMOTE_ADDR=f'203.0.113.{20 + index}',
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(int(client.session['_auth_user_id']), user.pk)

    def test_senha_incorreta_e_rejeitada_com_mensagem_generica(self):
        self.create_admin('admin@test.local', User.Role.ADMIN_TOTAL)
        response = self.post_login('admin@test.local')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'E-mail ou senha inválidos.')
        self.assertNotContains(response, 'não possui acesso')

    def test_quinta_falha_bloqueia_combinação_e_sexta_tentativa_e_rejeitada(self):
        self.create_admin('admin@test.local', User.Role.ADMIN_TOTAL)
        with self.assertLogs('aplicativo.security', level='WARNING') as captured:
            for _ in range(5):
                response = self.post_login('admin@test.local')
                self.assertEqual(response.status_code, 200)
            blocked = self.post_login('admin@test.local')

        self.assertEqual(blocked.status_code, 429)
        self.assertContains(
            blocked,
            'Muitas tentativas de acesso. Aguarde alguns minutos e tente novamente.',
            status_code=429,
        )
        self.assertNotIn(self.password, '\n'.join(captured.output))
        self.assertNotContains(blocked, self.password, status_code=429)
        self.assertTrue(LimiteSeguranca.objects.filter(escopo='LOGIN_ADMIN_COMBO').exists())

    def test_outro_ip_nao_compartilha_contador_da_combinacao(self):
        for _ in range(4):
            self.post_login('admin@test.local', ip='203.0.113.10')
        response = self.post_login('admin@test.local', ip='203.0.113.11')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'E-mail ou senha inválidos.')

    def test_identidade_agrega_falhas_de_ips_diferentes(self):
        for index in range(8):
            response = self.post_login(
                'alvo@test.local', ip=f'198.51.100.{index + 1}'
            )
            self.assertEqual(response.status_code, 200)
        blocked = self.post_login('ALVO@test.local', ip='198.51.100.20')
        self.assertEqual(blocked.status_code, 429)
        self.assertContains(blocked, 'Muitas tentativas de acesso', status_code=429)

    def test_limite_por_ip_agrega_identidades_diferentes(self):
        for index in range(20):
            response = self.post_login(
                f'variacao{index}@test.local', ip='203.0.113.90'
            )
            self.assertEqual(response.status_code, 200)
        blocked = self.post_login('outra-conta@test.local', ip='203.0.113.90')
        self.assertEqual(blocked.status_code, 429)

    def test_mensagem_nao_revela_existencia_da_conta(self):
        self.create_admin('admin-conhecido@test.local', User.Role.ADMIN_TOTAL)
        User.objects.create_user(email='cliente@test.local', password=self.password)
        desconhecido = self.post_login('inexistente@test.local')
        admin_conhecido = self.post_login(
            'admin-conhecido@test.local', ip='203.0.113.11'
        )
        cliente = self.post_login('cliente@test.local', password=self.password, ip='203.0.113.11')
        self.assertContains(desconhecido, 'E-mail ou senha inválidos.')
        self.assertContains(admin_conhecido, 'E-mail ou senha inválidos.')
        self.assertContains(cliente, 'E-mail ou senha inválidos.')
        self.assertNotContains(cliente, 'não possui acesso ao Manage')
        self.assertNotContains(desconhecido, self.password)
        self.assertNotContains(admin_conhecido, self.password)
        self.assertNotContains(cliente, self.password)
