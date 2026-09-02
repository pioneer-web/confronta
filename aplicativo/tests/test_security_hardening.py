from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.messages import get_messages

from administracao.models import User
from aplicativo.models import LimiteSeguranca, PerfilCliente
from aplicativo.session_keys import SESSION_CAR_ATUAL


@override_settings(
    LOGIN_FAILURE_LIMIT=3,
    LOGIN_FAILURE_WINDOW_SECONDS=900,
    LOGIN_BLOCK_SECONDS=900,
    CLIENT_LOGIN_IDENTITY_FAILURE_LIMIT=6,
    ADMIN_LOGIN_IDENTITY_FAILURE_LIMIT=6,
    LOGIN_IDENTITY_BLOCK_SECONDS=900,
    CLIENT_LOGIN_IP_FAILURE_LIMIT=20,
    ADMIN_LOGIN_IP_FAILURE_LIMIT=20,
    PUBLIC_MAX_REQUEST_BODY_BYTES=1024,
    CAR_LOOKUP_RATE_LIMIT_5MIN=2,
    CAR_LOOKUP_IP_RATE_LIMIT_5MIN=10,
    CAR_RATE_LIMIT_5MIN=2,
    CAR_RATE_LIMIT_HOUR=10,
    CAR_IP_RATE_LIMIT_5MIN=10,
)
class SecurityHardeningTests(TestCase):
    CAR = 'PE-2614105-9C74D4EF908C4BF4A177617BDC9C3D86'

    def setUp(self):
        self.user = User.objects.create_user(email='cliente@test.local', password='SenhaForte123!')
        PerfilCliente.objects.create(usuario=self.user, plano=PerfilCliente.Plano.BASICO)

    def test_tres_falhas_bloqueiam_login_temporariamente(self):
        url = reverse('aplicativo:login')
        for _ in range(2):
            response = self.client.post(url, {'email': self.user.email, 'password': 'errada'})
            self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {'email': self.user.email, 'password': 'errada'})
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'Limite de tentativas atingido', status_code=429)
        self.assertTrue(LimiteSeguranca.objects.filter(escopo='LOGIN_CLIENTE_COMBO').exists())

    def test_honeypot_bloqueia_automacao(self):
        response = self.client.post(reverse('aplicativo:login'), {
            'email': self.user.email,
            'password': 'SenhaForte123!',
            '_contact_website': 'https://bot.invalid',
        })
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertTrue(LimiteSeguranca.objects.filter(escopo='BOT_LOGIN_CLIENTE').exists())

    def test_corpo_grande_e_rejeitado_antes_do_login(self):
        response = self.client.post(
            reverse('aplicativo:login'),
            data='x' * 2048,
            content_type='text/plain',
        )
        self.assertEqual(response.status_code, 413)

    @patch('aplicativo.views.dashboard.ConsultaCarService.validar_existencia')
    def test_selecao_car_tem_rate_limit_antes_do_banco(self, validar):
        validar.return_value = {'cod_imovel': self.CAR}
        self.client.force_login(self.user)
        url = reverse('aplicativo:nova_consulta')

        self.assertEqual(self.client.post(url, {'car': self.CAR}).status_code, 302)
        self.assertEqual(self.client.post(url, {'car': self.CAR}).status_code, 302)
        terceira = self.client.post(url, {'car': self.CAR})
        mensagens = [str(m) for m in get_messages(terceira.wsgi_request)]
        self.assertTrue(any('Limite de consultas atingido' in m for m in mensagens))
        self.assertEqual(validar.call_count, 2)
        self.assertEqual(self.client.session.get(SESSION_CAR_ATUAL), self.CAR)

    @patch('aplicativo.views.dashboard.ConsultaCarService.executar')
    def test_refresh_da_tela_com_car_tambem_tem_rate_limit(self, executar):
        executar.return_value = {
            'imovel': {'cod_imovel': self.CAR, 'geometry': {'type': 'Polygon', 'coordinates': []}},
            'camadas': {}, 'analises_externas': {}, 'camadas_externas': {},
            'outros_cars': {}, 'alertas': {'tem_alerta': False, 'restricoes': {}},
            'restricoes': {},
        }
        self.client.force_login(self.user)
        session = self.client.session
        session[SESSION_CAR_ATUAL] = self.CAR
        session.save()
        url = reverse('aplicativo:inicio')

        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 200)
        terceira = self.client.get(url)
        self.assertEqual(terceira.status_code, 200)
        self.assertContains(terceira, 'Limite de consultas atingido')
        self.assertEqual(executar.call_count, 2)

    def test_mesma_conta_em_ips_diferentes_tambem_e_protegida(self):
        url = reverse('aplicativo:login')
        # Limite de identidade do teste = 6; cada tentativa usa IP diferente para
        # provar que a proteção não depende somente do endereço de origem.
        for i in range(5):
            response = self.client.post(
                url,
                {'email': self.user.email, 'password': 'errada'},
                REMOTE_ADDR=f'203.0.113.{10 + i}',
            )
            self.assertEqual(response.status_code, 200)
        sexta = self.client.post(
            url,
            {'email': self.user.email, 'password': 'errada'},
            REMOTE_ADDR='203.0.113.99',
        )
        self.assertEqual(sexta.status_code, 429)
        self.assertTrue(
            LimiteSeguranca.objects.filter(escopo='LOGIN_CLIENTE_IDENTIDADE').exists()
        )

    def test_banco_de_limite_nao_armazena_email_ou_ip_em_claro(self):
        for _ in range(3):
            self.client.post(reverse('aplicativo:login'), {
                'email': self.user.email,
                'password': 'errada',
            }, REMOTE_ADDR='203.0.113.10')
        valores = list(LimiteSeguranca.objects.values_list('chave_hash', flat=True))
        self.assertTrue(valores)
        self.assertTrue(all(self.user.email not in valor for valor in valores))
        self.assertTrue(all('203.0.113.10' not in valor for valor in valores))
