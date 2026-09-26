from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY
from django.test import TestCase, override_settings
from django.urls import reverse

from administracao.models import User
from aplicativo.models import PerfilCliente, PlanoComercial
from aplicativo.session_keys import SESSION_CICLO_CONTRATACAO
from billing.models import AsaasCheckout


@override_settings(
    ASAAS_API_KEY='sandbox-test-key',
    ASAAS_ENVIRONMENT='sandbox',
    ASAAS_CALLBACK_BASE_URL='https://checkout.test.local',
)
class CadastroClienteTests(TestCase):
    def setUp(self):
        self.plano = PlanoComercial.objects.get(slug='confronta')
        self.plano.nome = 'CONFRONTA'
        self.plano.nivel_acesso = PerfilCliente.Plano.TOTAL
        self.plano.preco_mensal = Decimal('67.90')
        self.plano.preco_anual = Decimal('598.80')
        self.plano.ativo = True
        self.plano.save()

    def _dados(self, email):
        return {
            'nome': 'Cliente Teste',
            'email': email,
            'telefone': '(81) 99999-1111',
            'password1': 'SenhaForte123!x',
            'password2': 'SenhaForte123!x',
        }

    def test_cadastro_mensal_cria_conta_sem_checkout(self):
        response = self.client.post(
            reverse('aplicativo:cadastro_mensal'),
            self._dados('mensal@test.local'),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse('aplicativo:cadastro_concluido'),
        )

        user = User.objects.get(email='mensal@test.local')
        perfil = user.perfil_cliente

        self.assertEqual(
            perfil.plano,
            PerfilCliente.Plano.SEM_PLANO,
        )
        self.assertEqual(
            perfil.plano_desejado_comercial,
            self.plano,
        )
        self.assertFalse(perfil.renovacao_automatica)

        self.assertFalse(
            AsaasCheckout.objects.filter(perfil=perfil).exists()
        )

        self.assertEqual(
            self.client.session[SESSION_CICLO_CONTRATACAO],
            AsaasCheckout.Ciclo.MONTHLY,
        )

    def test_cadastro_neutro_abre_formulario_sem_escolha_de_plano(self):
        response = self.client.get(reverse('aplicativo:cadastro'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Criar conta grátis')
        self.assertNotContains(response, 'Modalidade selecionada')

    def test_cadastro_neutro_cria_cliente_sem_checkout_e_abre_mapa(self):
        response = self.client.post(
            reverse('aplicativo:cadastro'),
            self._dados('neutro@test.local'),
        )

        self.assertRedirects(
            response,
            reverse('aplicativo:inicio'),
            fetch_redirect_response=False,
        )
        user = User.objects.get(email='neutro@test.local')
        perfil = user.perfil_cliente
        self.assertEqual(perfil.plano, PerfilCliente.Plano.SEM_PLANO)
        self.assertTrue(perfil.ativo)
        self.assertFalse(perfil.renovacao_automatica)
        self.assertIsNone(perfil.plano_desejado)
        self.assertIsNone(perfil.plano_desejado_comercial)
        self.assertFalse(AsaasCheckout.objects.filter(perfil=perfil).exists())
        self.assertEqual(str(self.client.session[SESSION_KEY]), str(user.pk))
        self.assertTrue(perfil.token_sessao_ativa)
        dashboard = self.client.get(reverse('aplicativo:inicio'))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, 'id="map"')
        self.assertContains(dashboard, 'data-free-mode="true"')

    def test_tela_anual_exibe_parcelamento_e_nao_pede_cpf(self):
        response = self.client.get(
            reverse('aplicativo:cadastro_anual')
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Realizar cadastro')
        self.assertContains(response, 'R$ 598,80')
        self.assertContains(response, '6x de')
        self.assertContains(response, 'R$ 99,80')
        self.assertContains(response, 'name="telefone"')
        self.assertContains(response, 'name="password1"')
        self.assertContains(response, 'name="email"')
        self.assertNotContains(response, 'name="cpf"')
        self.assertNotContains(response, 'name="empresa"')

    @patch('billing.services.checkout.AsaasClient.criar_checkout')
    def test_checkout_anual_e_compra_parcelavel_nao_recorrente(
        self,
        criar_checkout_mock,
    ):
        criar_checkout_mock.return_value = {
            'id': 'chk_test_yearly',
            'link': (
                'https://sandbox.asaas.com/'
                'checkoutSession/show/chk_test_yearly'
            ),
        }

        cadastro = self.client.post(
            reverse('aplicativo:cadastro_anual'),
            self._dados('anual@test.local'),
        )

        self.assertEqual(
            cadastro.url,
            reverse('aplicativo:cadastro_concluido'),
        )
        self.assertEqual(AsaasCheckout.objects.count(), 0)
        criar_checkout_mock.assert_not_called()

        response = self.client.post(
            reverse('billing:iniciar_checkout'),
            {'ciclo': AsaasCheckout.Ciclo.YEARLY},
        )

        self.assertEqual(response.status_code, 302)

        checkout = AsaasCheckout.objects.get(
            perfil__usuario__email='anual@test.local'
        )

        self.assertEqual(
            checkout.ciclo,
            AsaasCheckout.Ciclo.YEARLY,
        )
        self.assertEqual(
            checkout.valor,
            Decimal('598.80'),
        )

        payload = criar_checkout_mock.call_args.args[0]

        self.assertEqual(
            payload['billingTypes'],
            ['CREDIT_CARD'],
        )
        self.assertEqual(
            payload['chargeTypes'],
            ['DETACHED', 'INSTALLMENT'],
        )
        self.assertEqual(
            payload['installment']['maxInstallmentCount'],
            PlanoComercial.PARCELAS_ANUAL,
        )
        self.assertEqual(
            payload['items'][0]['value'],
            598.8,
        )

        self.assertNotIn('subscription', payload)
        self.assertNotIn('customer', payload)
        self.assertNotIn('customerData', payload)

    @patch('billing.services.checkout.AsaasClient.criar_checkout')
    def test_checkout_mensal_continua_recorrente(
        self,
        criar_checkout_mock,
    ):
        criar_checkout_mock.return_value = {
            'id': 'chk_test_monthly',
            'link': (
                'https://sandbox.asaas.com/'
                'checkoutSession/show/chk_test_monthly'
            ),
        }

        self.client.post(
            reverse('aplicativo:cadastro_mensal'),
            self._dados('recorrente@test.local'),
        )

        response = self.client.post(
            reverse('billing:iniciar_checkout'),
            {'ciclo': AsaasCheckout.Ciclo.MONTHLY},
        )

        self.assertEqual(response.status_code, 302)

        payload = criar_checkout_mock.call_args.args[0]

        self.assertEqual(
            payload['chargeTypes'],
            ['RECURRENT'],
        )
        self.assertEqual(
            payload['subscription']['cycle'],
            AsaasCheckout.Ciclo.MONTHLY,
        )
        self.assertNotIn('installment', payload)
        self.assertNotIn('customerData', payload)
        self.assertNotIn('customer', payload)
