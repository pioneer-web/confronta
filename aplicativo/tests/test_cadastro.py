from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from administracao.models import User
from aplicativo.models import PerfilCliente, PlanoComercial
from billing.models import AsaasCheckout


@override_settings(ASAAS_API_KEY='sandbox-test-key', ASAAS_ENVIRONMENT='sandbox', ASAAS_CALLBACK_BASE_URL='https://checkout.test.local')
class CadastroClienteTests(TestCase):
    def setUp(self):
        self.plano = PlanoComercial.objects.get(slug='confronta')
        self.plano.nome = 'CONFRONTA'
        self.plano.nivel_acesso = PerfilCliente.Plano.TOTAL
        self.plano.preco_mensal = '67.90'
        self.plano.preco_anual = '598.80'
        self.plano.ativo = True
        self.plano.save()

    @patch('billing.services.checkout.AsaasClient.criar_checkout')
    def test_cadastro_cria_conta_sem_plano_e_redireciona_checkout(self, criar_checkout_mock):
        criar_checkout_mock.return_value = {
            'id': 'chk_test_001',
            'link': 'https://sandbox.asaas.com/checkoutSession/show/chk_test_001',
        }
        response = self.client.post(reverse('aplicativo:cadastro'), {
            'nome': 'Cliente Teste',
            'email': 'novo.cliente@test.local',
            'telefone': '(81) 99999-1111',
            'password1': 'SenhaForte123!x',
            'password2': 'SenhaForte123!x',
            'ciclo': 'MONTHLY',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, 'https://sandbox.asaas.com/checkoutSession/show/chk_test_001')
        user = User.objects.get(email='novo.cliente@test.local')
        perfil = user.perfil_cliente
        self.assertEqual(perfil.plano, PerfilCliente.Plano.SEM_PLANO)
        self.assertEqual(perfil.plano_desejado_comercial, self.plano)
        self.assertFalse(perfil.renovacao_automatica)
        checkout = AsaasCheckout.objects.get(perfil=perfil)
        self.assertEqual(checkout.ciclo, AsaasCheckout.Ciclo.MONTHLY)
        self.assertEqual(str(checkout.valor), '67.90')

    def test_tela_publica_exibe_cadastro_e_nao_pede_cpf_ou_empresa(self):
        response = self.client.get(reverse('aplicativo:cadastro'), {'ciclo': 'YEARLY'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Criar conta e ir para o pagamento')
        self.assertContains(response, 'R$ 49,90')
        self.assertContains(response, 'name="password1"')
        self.assertContains(response, 'name="email"')
        self.assertNotContains(response, 'name="cpf"')
        self.assertNotContains(response, 'name="empresa"')

    @patch('billing.services.checkout.AsaasClient.criar_checkout')
    def test_anual_envia_valor_integral_e_ciclo_yearly(self, criar_checkout_mock):
        criar_checkout_mock.return_value = {
            'id': 'chk_test_yearly',
            'link': 'https://sandbox.asaas.com/checkoutSession/show/chk_test_yearly',
        }
        response = self.client.post(reverse('aplicativo:cadastro'), {
            'nome': 'Cliente Anual',
            'email': 'anual@test.local',
            'telefone': '(81) 99999-2222',
            'password1': 'SenhaForte123!x',
            'password2': 'SenhaForte123!x',
            'ciclo': 'YEARLY',
        })
        self.assertEqual(response.status_code, 302)
        checkout = AsaasCheckout.objects.get(perfil__usuario__email='anual@test.local')
        self.assertEqual(checkout.ciclo, AsaasCheckout.Ciclo.YEARLY)
        self.assertEqual(str(checkout.valor), '598.80')
        payload = criar_checkout_mock.call_args.args[0]
        self.assertEqual(payload['billingTypes'], ['CREDIT_CARD'])
        self.assertEqual(payload['chargeTypes'], ['RECURRENT'])
        self.assertEqual(payload['subscription']['cycle'], 'YEARLY')
        self.assertEqual(payload['items'][0]['value'], 598.8)
        self.assertNotIn('customer', payload)
        self.assertNotIn('customerData', payload)

    @patch('billing.services.checkout.AsaasClient.criar_checkout')
    def test_checkout_nao_envia_customer_data_parcial(self, criar_checkout_mock):
        criar_checkout_mock.return_value = {
            'id': 'chk_sem_customer_data',
            'link': 'https://sandbox.asaas.com/checkoutSession/show/chk_sem_customer_data',
        }
        response = self.client.post(reverse('aplicativo:cadastro'), {
            'nome': 'Cliente Sem Dados Financeiros',
            'email': 'semfinanceiro@test.local',
            'telefone': '(81) 99999-5555',
            'password1': 'SenhaForte123!x',
            'password2': 'SenhaForte123!x',
            'ciclo': 'MONTHLY',
        })
        self.assertEqual(response.status_code, 302)
        payload = criar_checkout_mock.call_args.args[0]
        self.assertNotIn('customerData', payload)
        self.assertNotIn('customer', payload)

