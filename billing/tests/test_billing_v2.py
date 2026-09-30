from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.test import RequestFactory
from django.utils import timezone

from administracao.models import User
from aplicativo.models import PerfilCliente, PlanoComercial
from billing.models import AsaasCheckout, AssinaturaAsaas, Cupom, CupomUso, EventoWebhookAsaas
from billing.services.cupons import CupomInvalido, calcular_cupom, consumir_cupom
from billing.services.asaas import AsaasAPIError
from billing.services.sanitizacao import sanitizar_evento, sanitizar_resposta
from billing.services.webhooks import processar_evento
from billing.services.checkout import criar_checkout, criar_checkout_renovacao


@override_settings(BILLING_RENEWAL_NOTICE_DAYS=15)
class BillingV2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='billing-v2@example.test', password='NotARealPass123!')
        self.plan = PlanoComercial.objects.get(slug='confronta')
        self.plan.preco_mensal = Decimal('67.90')
        self.plan.preco_anual = Decimal('598.80')
        self.plan.save()
        self.profile = PerfilCliente.objects.create(
            usuario=self.user,
            telefone='81999990000',
            plano=PerfilCliente.Plano.SEM_PLANO,
            plano_desejado=PerfilCliente.Plano.TOTAL,
            plano_desejado_comercial=self.plan,
        )

    def _checkout_mensal_aberto(self):
        return AsaasCheckout.objects.create(
            usuario=self.user, perfil=self.profile, plano=self.plan,
            ciclo=AsaasCheckout.Ciclo.MONTHLY,
            modalidade=AsaasCheckout.Modalidade.MONTHLY,
            valor='67.90', valor_original='67.90', valor_final='67.90',
            asaas_checkout_id='4c2f943f-ee81-4015-92ae-a9f5427c0d1e',
            status=AsaasCheckout.Status.ACTIVE,
        )

    @override_settings(ASAAS_CALLBACK_BASE_URL='https://sandbox.example.test')
    def test_invalid_action_de_checkout_encerrado_permite_criar_anual(self):
        antigo = self._checkout_mensal_aberto()
        client = Mock()
        client.cancelar_checkout.side_effect = AsaasAPIError(
            'Asaas retornou HTTP 400.', status_code=400,
            response={'errors': [{'code': 'invalid_action', 'description': 'O Checkout não está ativo para ser cancelado.'}]},
        )
        client.criar_checkout.return_value = {'id': 'chk_annual', 'link': 'https://sandbox.asaas.com/annual'}
        request = RequestFactory().post('/billing/checkout/')

        with patch('billing.services.checkout.AsaasClient.from_settings', return_value=client):
            novo = criar_checkout(request, self.profile, AsaasCheckout.Ciclo.YEARLY)

        antigo.refresh_from_db()
        self.assertEqual(antigo.status, AsaasCheckout.Status.CANCELED)
        self.assertEqual(novo.ciclo, AsaasCheckout.Ciclo.YEARLY)
        self.assertEqual(novo.status, AsaasCheckout.Status.ACTIVE)
        client.criar_checkout.assert_called_once()

    def test_http_400_com_outro_codigo_nao_permite_substituir_checkout(self):
        antigo = self._checkout_mensal_aberto()
        client = Mock()
        client.cancelar_checkout.side_effect = AsaasAPIError(
            'Asaas retornou HTTP 400.', status_code=400,
            response={'errors': [{'code': 'invalid_parameter', 'description': 'Falha ao cancelar.'}]},
        )
        request = RequestFactory().post('/billing/checkout/')

        with patch('billing.services.checkout.AsaasClient.from_settings', return_value=client):
            with self.assertRaises(RuntimeError):
                criar_checkout(request, self.profile, AsaasCheckout.Ciclo.YEARLY)

        antigo.refresh_from_db()
        self.assertEqual(antigo.status, AsaasCheckout.Status.ACTIVE)
        client.criar_checkout.assert_not_called()

    def test_coupon_percentual_e_valor_fixo_calculados_no_backend(self):
        percentual = Cupom.objects.create(codigo='V2VINTE', tipo_desconto='PERCENTUAL', valor_desconto='20')
        fixo = Cupom.objects.create(codigo='V2FIXO', tipo_desconto='VALOR_FIXO', valor_desconto='50')
        _, desconto_pct, final_pct = calcular_cupom('v2vinte', AsaasCheckout.Ciclo.YEARLY, Decimal('598.80'))
        _, desconto_fixo, final_fixo = calcular_cupom(fixo.codigo, AsaasCheckout.Ciclo.YEARLY, Decimal('598.80'))
        self.assertEqual((desconto_pct, final_pct), (Decimal('119.76'), Decimal('479.04')))
        self.assertEqual((desconto_fixo, final_fixo), (Decimal('50.00'), Decimal('548.80')))

    def test_coupon_expirado_e_limite_de_usos_sao_rejeitados(self):
        expirado = Cupom.objects.create(
            codigo='V2EXPIRADO', tipo_desconto='VALOR_FIXO', valor_desconto='10',
            validade_fim=timezone.now() - timedelta(days=1),
        )
        esgotado = Cupom.objects.create(
            codigo='V2ESGOTADO', tipo_desconto='PERCENTUAL', valor_desconto='10',
            limite_total_usos=1, quantidade_usos=1,
        )
        for code in (expirado.codigo, esgotado.codigo):
            with self.assertRaises(CupomInvalido):
                calcular_cupom(code, AsaasCheckout.Ciclo.YEARLY, Decimal('598.80'))

    def test_uso_de_cupom_e_consumido_apenas_uma_vez(self):
        coupon = Cupom.objects.create(codigo='V2UNICO', tipo_desconto='VALOR_FIXO', valor_desconto='5')
        checkout = AsaasCheckout.objects.create(
            usuario=self.user, perfil=self.profile, plano=self.plan,
            ciclo=AsaasCheckout.Ciclo.YEARLY,
            modalidade=AsaasCheckout.Modalidade.YEARLY_INSTALLMENT,
            valor='593.80', valor_original='598.80', valor_desconto='5', valor_final='593.80',
            asaas_checkout_id='chk_v2_coupon', status=AsaasCheckout.Status.PAID,
        )
        CupomUso.objects.create(cupom=coupon, checkout=checkout)
        self.assertTrue(consumir_cupom(checkout))
        self.assertFalse(consumir_cupom(checkout))
        coupon.refresh_from_db()
        self.assertEqual(coupon.quantidade_usos, 1)

    def test_webhook_checkout_paid_consumo_idempotente_e_historico_de_preco(self):
        coupon = Cupom.objects.create(codigo='V2WEBHOOK', tipo_desconto='PERCENTUAL', valor_desconto='10')
        checkout = AsaasCheckout.objects.create(
            usuario=self.user, perfil=self.profile, plano=self.plan,
            ciclo=AsaasCheckout.Ciclo.YEARLY,
            modalidade=AsaasCheckout.Modalidade.YEARLY_INSTALLMENT,
            parcelas_maximas_ofertadas=6,
            valor='538.92', valor_original='598.80', valor_desconto='59.88', valor_final='538.92',
            cupom=coupon.codigo, asaas_checkout_id='chk_v2_paid', status=AsaasCheckout.Status.ACTIVE,
        )
        CupomUso.objects.create(cupom=coupon, checkout=checkout)
        payload = {'id': 'chk_v2_paid', 'customer': 'cus_technical', 'status': 'PAID', 'installmentCount': 3}
        first = EventoWebhookAsaas.objects.create(event_id='evt_v2_a', event_type='CHECKOUT_PAID', payload={'checkout': payload})
        second = EventoWebhookAsaas.objects.create(event_id='evt_v2_b', event_type='CHECKOUT_PAID', payload={'checkout': payload})
        processar_evento(first)
        processar_evento(second)
        coupon.refresh_from_db()
        assinatura = AssinaturaAsaas.objects.get(perfil=self.profile, atual=True)
        self.assertEqual(coupon.quantidade_usos, 1)
        self.assertEqual(assinatura.valor_original, Decimal('598.80'))
        self.assertEqual(assinatura.valor_desconto, Decimal('59.88'))
        self.assertEqual(assinatura.valor_final, Decimal('538.92'))
        self.assertEqual(assinatura.parcelas_contratadas, 3)

    def test_varredura_marca_reautorizacao_sem_cobrar_ou_tirar_acesso_antes_da_data(self):
        assinatura = AssinaturaAsaas.objects.create(
            perfil=self.profile, plano=self.plan,
            ciclo=AsaasCheckout.Ciclo.YEARLY,
            modalidade=AsaasCheckout.Modalidade.YEARLY_INSTALLMENT,
            valor='598.80', valor_original='598.80', valor_final='598.80',
            parcelas_maximas_ofertadas=6,
            acesso_ate=timezone.localdate() + timedelta(days=10),
            status=AssinaturaAsaas.Status.ACTIVE,
        )
        self.profile.ativo = True
        self.profile.save(update_fields=['ativo'])
        call_command('marcar_renovacoes_anuais')
        assinatura.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(assinatura.renovacao_status, AssinaturaAsaas.RenovacaoStatus.PENDING_REAUTHORIZATION)
        self.assertEqual(assinatura.status, AssinaturaAsaas.Status.ACTIVE)
        self.assertTrue(self.profile.ativo)
        self.assertEqual(assinatura.pagamentos.count(), 0)
        call_command('marcar_renovacoes_anuais')
        assinatura.refresh_from_db()
        self.assertEqual(assinatura.renovacao_status, AssinaturaAsaas.RenovacaoStatus.PENDING_REAUTHORIZATION)

    def test_payloads_preservam_ids_tecnicos_e_removem_dados_financeiros_pessoais(self):
        clean_event = sanitizar_evento({
            'id': 'evt', 'event': 'PAYMENT_CONFIRMED',
            'payment': {
                'id': 'pay', 'customer': 'cus', 'installmentId': 'ins', 'installmentCount': 6,
                'billingType': 'CREDIT_CARD', 'value': 100, 'creditCardToken': 'never-store',
                'cpfCnpj': 'never-store', 'address': {'street': 'private'},
            },
        })
        clean_response = sanitizar_resposta({'id': 'chk', 'link': 'https://sandbox.asaas.com/x', 'creditCardToken': 'never-store'})
        self.assertEqual(clean_event['payment']['installmentCount'], 6)
        self.assertNotIn('creditCardToken', clean_event['payment'])
        self.assertNotIn('cpfCnpj', clean_event['payment'])
        self.assertNotIn('creditCardToken', clean_response)

    @override_settings(ASAAS_CALLBACK_BASE_URL='https://sandbox.example.test')
    def test_monthly_e_annual_cash_enviam_recorrencia_correta(self):
        for cycle, mode, expected_cycle in (
            (AsaasCheckout.Ciclo.MONTHLY, None, 'MONTHLY'),
            (AsaasCheckout.Ciclo.YEARLY, AsaasCheckout.Modalidade.YEARLY_CASH, 'YEARLY'),
        ):
            client = Mock()
            client.criar_checkout.return_value = {'id': f'chk_{expected_cycle}', 'link': 'https://sandbox.asaas.com/checkout'}
            request = RequestFactory().post('/billing/checkout/')
            with patch('billing.services.checkout.AsaasClient.from_settings', return_value=client):
                criar_checkout(request, self.profile, cycle, mode)
            payload = client.criar_checkout.call_args.args[0]
            self.assertEqual(payload['chargeTypes'], ['RECURRENT'])
            self.assertEqual(payload['subscription']['cycle'], expected_cycle)

    @override_settings(ASAAS_CALLBACK_BASE_URL='https://sandbox.example.test')
    def test_annual_installment_renewal_creates_separate_checkout_without_extending_access(self):
        old_end = timezone.localdate() + timedelta(days=8)
        signature = AssinaturaAsaas.objects.create(
            perfil=self.profile, plano=self.plan,
            ciclo=AsaasCheckout.Ciclo.YEARLY,
            modalidade=AsaasCheckout.Modalidade.YEARLY_INSTALLMENT,
            valor='598.80', valor_original='598.80', valor_final='598.80',
            parcelas_maximas_ofertadas=4,
            acesso_ate=old_end, status=AssinaturaAsaas.Status.ACTIVE, atual=True,
            renovacao_pendente=True,
            renovacao_status=AssinaturaAsaas.RenovacaoStatus.PENDING_REAUTHORIZATION,
        )
        client = Mock()
        client.criar_checkout.return_value = {'id': 'chk_renewal_v2', 'link': 'https://sandbox.asaas.com/renewal'}
        request = RequestFactory().post('/billing/renew/')
        with patch('billing.services.checkout.AsaasClient.from_settings', return_value=client):
            checkout = criar_checkout_renovacao(request, self.profile, signature)
        payload = client.criar_checkout.call_args.args[0]
        self.assertEqual(payload['chargeTypes'], ['INSTALLMENT'])
        self.assertEqual(payload['installment']['maxInstallmentCount'], 4)
        self.assertEqual(checkout.renovacao_de_id, signature.pk)
        self.assertEqual(checkout.parcelas_maximas_ofertadas, 4)
        self.assertIsNone(checkout.parcelas_contratadas)
        signature.refresh_from_db()
        self.assertEqual(signature.acesso_ate, old_end)
        self.assertEqual(signature.renovacao_status, AssinaturaAsaas.RenovacaoStatus.CHECKOUT_CREATED)

    def test_paid_annual_installment_renewal_starts_at_later_of_today_or_previous_end(self):
        previous_end = timezone.localdate() + timedelta(days=4)
        previous = AssinaturaAsaas.objects.create(
            perfil=self.profile, plano=self.plan,
            ciclo=AsaasCheckout.Ciclo.YEARLY,
            modalidade=AsaasCheckout.Modalidade.YEARLY_INSTALLMENT,
            valor='598.80', valor_original='598.80', valor_final='598.80',
            acesso_ate=previous_end, status=AssinaturaAsaas.Status.ACTIVE, atual=True,
            renovacao_pendente=True,
            renovacao_status=AssinaturaAsaas.RenovacaoStatus.CHECKOUT_CREATED,
        )
        renewal = AsaasCheckout.objects.create(
            usuario=self.user, perfil=self.profile, plano=self.plan,
            renovacao_de=previous,
            ciclo=AsaasCheckout.Ciclo.YEARLY,
            modalidade=AsaasCheckout.Modalidade.YEARLY_INSTALLMENT,
            valor='598.80', valor_original='598.80', valor_final='598.80',
            parcelas_maximas_ofertadas=6, asaas_checkout_id='chk_paid_renewal_v2',
            status=AsaasCheckout.Status.ACTIVE,
        )
        event = EventoWebhookAsaas.objects.create(
            event_id='evt_paid_renewal_v2', event_type='CHECKOUT_PAID',
            payload={'checkout': {'id': renewal.asaas_checkout_id, 'customer': 'cus_renewal'}},
        )
        processar_evento(event)
        new_signature = AssinaturaAsaas.objects.get(checkout_origem=renewal)
        expected_base = max(timezone.localdate(), previous_end)
        self.assertEqual(new_signature.acesso_ate, expected_base.replace(year=expected_base.year + 1))
        previous.refresh_from_db()
        self.assertEqual(previous.renovacao_status, AssinaturaAsaas.RenovacaoStatus.RENEWED)
        self.assertEqual(AssinaturaAsaas.objects.filter(perfil=self.profile, atual=True).count(), 1)

    def test_annual_cash_next_subscription_payment_extends_once_after_confirmation(self):
        paid_until = timezone.localdate() - timedelta(days=1)
        signature = AssinaturaAsaas.objects.create(
            perfil=self.profile, plano=self.plan,
            ciclo=AsaasCheckout.Ciclo.YEARLY,
            modalidade=AsaasCheckout.Modalidade.YEARLY_CASH,
            valor='598.80', valor_original='598.80', valor_final='598.80',
            asaas_subscription_id='sub_yearly_v2', acesso_ate=paid_until,
            status=AssinaturaAsaas.Status.ACTIVE, atual=True,
        )
        self.profile.ativo = True
        self.profile.fim_acesso = paid_until
        self.profile.save(update_fields=['ativo', 'fim_acesso'])
        event = EventoWebhookAsaas.objects.create(
            event_id='evt_yearly_payment_v2', event_type='PAYMENT_CONFIRMED',
            payload={'payment': {
                'id': 'pay_yearly_v2', 'subscription': 'sub_yearly_v2',
                'customer': 'cus_yearly', 'status': 'CONFIRMED',
                'billingType': 'CREDIT_CARD', 'value': 598.80,
                'dueDate': str(timezone.localdate()),
            }},
        )
        processar_evento(event)
        signature.refresh_from_db()
        first_extension = signature.acesso_ate
        expected = timezone.localdate().replace(year=timezone.localdate().year + 1)
        self.assertEqual(first_extension, expected)
        duplicate = EventoWebhookAsaas.objects.create(
            event_id='evt_yearly_payment_duplicate_v2', event_type='PAYMENT_RECEIVED',
            payload={'payment': {
                'id': 'pay_yearly_v2', 'subscription': 'sub_yearly_v2',
                'customer': 'cus_yearly', 'status': 'RECEIVED',
                'billingType': 'CREDIT_CARD', 'value': 598.80,
                'dueDate': str(timezone.localdate()),
            }},
        )
        processar_evento(duplicate)
        signature.refresh_from_db()
        self.assertEqual(signature.acesso_ate, first_extension)
