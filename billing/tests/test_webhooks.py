import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from administracao.models import User
from aplicativo.models import PerfilCliente, PlanoComercial
from billing.models import (
    AsaasCheckout, AssinaturaAsaas, EventoWebhookAsaas, PagamentoAsaas,
)
from billing.services.webhooks import processar_evento


@override_settings(ASAAS_WEBHOOK_TOKEN='t' * 48, BILLING_GRACE_DAYS=5)
class AsaasWebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='billing@test.local',
            password='SenhaForte123!x',
            first_name='Cliente Billing',
        )
        self.plano = PlanoComercial.objects.get(slug='confronta')
        self.plano.nome = 'CONFRONTA'
        self.plano.nivel_acesso = PerfilCliente.Plano.TOTAL
        self.plano.preco_mensal = '67.90'
        self.plano.preco_anual = '598.80'
        self.plano.ativo = True
        self.plano.save()
        self.perfil = PerfilCliente.objects.create(
            usuario=self.user,
            telefone='81999990000',
            plano=PerfilCliente.Plano.SEM_PLANO,
            plano_desejado=PerfilCliente.Plano.TOTAL,
            plano_desejado_comercial=self.plano,
        )
        self.checkout = AsaasCheckout.objects.create(
            usuario=self.user,
            perfil=self.perfil,
            plano=self.plano,
            ciclo=AsaasCheckout.Ciclo.MONTHLY,
            valor='67.90',
            asaas_checkout_id='chk_001',
            status=AsaasCheckout.Status.ACTIVE,
        )

    def _enviar(self, payload, token=None):
        return self.client.post(
            reverse('billing:webhook_asaas'),
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_ASAAS_ACCESS_TOKEN=token or ('t' * 48),
        )

    def test_webhook_persiste_rapido_e_duplicate_e_idempotente(self):
        payload = {
            'id': 'evt_001',
            'event': 'CHECKOUT_PAID',
            'checkout': {'id': 'chk_001', 'customer': 'cus_001', 'status': 'PAID'},
        }
        first = self._enviar(payload)
        second = self._enviar(payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(EventoWebhookAsaas.objects.filter(event_id='evt_001').count(), 1)
        self.assertEqual(AssinaturaAsaas.objects.count(), 0)

        evento = EventoWebhookAsaas.objects.get(event_id='evt_001')
        processar_evento(evento)
        self.assertEqual(AssinaturaAsaas.objects.count(), 1)
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.plano, PerfilCliente.Plano.TOTAL)
        self.assertTrue(self.perfil.renovacao_automatica)

        processar_evento(evento)
        self.assertEqual(AssinaturaAsaas.objects.count(), 1)

    def test_subscription_event_que_chega_antes_fica_pendente_e_depois_vincula(self):
        payload_sub = {
            'id': 'evt_sub_001',
            'event': 'SUBSCRIPTION_CREATED',
            'subscription': {
                'id': 'sub_001', 'customer': 'cus_001', 'status': 'ACTIVE',
                'nextDueDate': '2026-09-30',
            },
        }
        self._enviar(payload_sub)
        evento_sub = EventoWebhookAsaas.objects.get(event_id='evt_sub_001')
        processar_evento(evento_sub)
        evento_sub.refresh_from_db()
        self.assertEqual(evento_sub.status, EventoWebhookAsaas.Status.PENDING)

        payload_paid = {
            'id': 'evt_paid_002',
            'event': 'CHECKOUT_PAID',
            'checkout': {'id': 'chk_001', 'customer': 'cus_001', 'status': 'PAID'},
        }
        self._enviar(payload_paid)
        processar_evento(EventoWebhookAsaas.objects.get(event_id='evt_paid_002'))
        processar_evento(evento_sub)

        assinatura = AssinaturaAsaas.objects.get(perfil=self.perfil, atual=True)
        self.assertEqual(assinatura.asaas_subscription_id, 'sub_001')
        self.assertEqual(assinatura.status, AssinaturaAsaas.Status.ACTIVE)

    def test_pagamento_atrasado_aplica_carencia_e_confirmado_renova(self):
        paid = {
            'id': 'evt_paid_003', 'event': 'CHECKOUT_PAID',
            'checkout': {'id': 'chk_001', 'customer': 'cus_001', 'status': 'PAID'},
        }
        self._enviar(paid)
        processar_evento(EventoWebhookAsaas.objects.get(event_id='evt_paid_003'))
        assinatura = AssinaturaAsaas.objects.get(perfil=self.perfil, atual=True)
        assinatura.asaas_subscription_id = 'sub_001'
        assinatura.save(update_fields=['asaas_subscription_id', 'atualizado_em'])

        overdue = {
            'id': 'evt_pay_overdue', 'event': 'PAYMENT_OVERDUE',
            'payment': {
                'id': 'pay_001', 'subscription': 'sub_001', 'customer': 'cus_001',
                'billingType': 'CREDIT_CARD', 'value': 67.90, 'dueDate': '2026-08-30',
                'status': 'OVERDUE',
            },
        }
        self._enviar(overdue)
        processar_evento(EventoWebhookAsaas.objects.get(event_id='evt_pay_overdue'))
        assinatura.refresh_from_db(); self.perfil.refresh_from_db()
        self.assertEqual(assinatura.status, AssinaturaAsaas.Status.PAST_DUE)
        self.assertGreaterEqual(self.perfil.fim_acesso, timezone.localdate())

        confirmed = {
            'id': 'evt_pay_confirmed', 'event': 'PAYMENT_CONFIRMED',
            'payment': {
                'id': 'pay_001', 'subscription': 'sub_001', 'customer': 'cus_001',
                'billingType': 'CREDIT_CARD', 'value': 67.90, 'dueDate': '2026-08-30',
                'status': 'CONFIRMED',
            },
        }
        self._enviar(confirmed)
        processar_evento(EventoWebhookAsaas.objects.get(event_id='evt_pay_confirmed'))
        assinatura.refresh_from_db()
        self.assertEqual(assinatura.status, AssinaturaAsaas.Status.ACTIVE)
        self.assertEqual(PagamentoAsaas.objects.filter(asaas_payment_id='pay_001').count(), 1)

    def test_webhook_token_invalido_retorna_403(self):
        response = self._enviar({'id': 'evt_bad', 'event': 'CHECKOUT_PAID'}, token='errado')
        self.assertEqual(response.status_code, 403)


    def test_status_assinatura_informa_acesso_apos_checkout_pago(self):
        self.client.force_login(self.user)
        pending = self.client.get(reverse('billing:status_assinatura'))
        self.assertEqual(pending.status_code, 200)
        self.assertFalse(pending.json()['acesso_confirmado'])

        payload = {
            'id': 'evt_status_paid',
            'event': 'CHECKOUT_PAID',
            'checkout': {'id': 'chk_001', 'customer': 'cus_status', 'status': 'PAID'},
        }
        self._enviar(payload)
        processar_evento(EventoWebhookAsaas.objects.get(event_id='evt_status_paid'))

        confirmed = self.client.get(reverse('billing:status_assinatura'))
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(confirmed.json()['acesso_confirmado'])
        self.assertEqual(confirmed.json()['status'], AssinaturaAsaas.Status.ACTIVE)

    def test_tela_sucesso_nao_expoe_termo_webhook_ao_cliente(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('billing:checkout_sucesso'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CONFRONTA está confirmando sua assinatura')
        self.assertNotContains(response, 'webhook')
