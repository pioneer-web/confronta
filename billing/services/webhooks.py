import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from billing.models import (
    AsaasCheckout,
    AssinaturaAsaas,
    EventoWebhookAsaas,
    PagamentoAsaas,
)


def _to_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    return parse_date(str(value)[:10])


def _to_decimal(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _add_cycle(base_date, ciclo):
    if base_date is None:
        base_date = timezone.localdate()
    if ciclo == AsaasCheckout.Ciclo.YEARLY:
        year, month = base_date.year + 1, base_date.month
    else:
        year = base_date.year + (1 if base_date.month == 12 else 0)
        month = 1 if base_date.month == 12 else base_date.month + 1
    day = min(base_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _grace_until(due_date):
    grace = int(getattr(settings, 'BILLING_GRACE_DAYS', 5))
    return (due_date or timezone.localdate()) + timedelta(days=grace)


def _assinatura_por_ids(subscription_id='', customer_id=''):
    qs = AssinaturaAsaas.objects.filter(atual=True)
    if subscription_id:
        encontrada = qs.filter(asaas_subscription_id=subscription_id).first()
        if encontrada:
            return encontrada
    if customer_id:
        return qs.filter(asaas_customer_id=customer_id).order_by('-criado_em').first()
    return None


@transaction.atomic
def _ativar_checkout(event_type, payload):
    data = payload.get('checkout') or {}
    checkout_id = data.get('id')
    if not checkout_id:
        return EventoWebhookAsaas.Status.ERROR, 'Evento de Checkout sem checkout.id.'

    checkout = AsaasCheckout.objects.select_related('perfil', 'plano').filter(asaas_checkout_id=checkout_id).first()
    if checkout is None:
        return EventoWebhookAsaas.Status.PENDING, f'Checkout {checkout_id} ainda não possui vínculo local.'

    customer_id = data.get('customer') or ''
    if customer_id:
        checkout.asaas_customer_id = customer_id

    if event_type == 'CHECKOUT_PAID':
        checkout.status = AsaasCheckout.Status.PAID
        checkout.pago_em = checkout.pago_em or timezone.now()
        checkout.resposta_asaas = data
        checkout.save()

        atual = AssinaturaAsaas.objects.filter(perfil=checkout.perfil, atual=True).first()
        if atual and atual.checkout_origem_id != checkout.id:
            atual.atual = False
            atual.encerrado_em = atual.encerrado_em or timezone.now()
            atual.save(update_fields=['atual', 'encerrado_em', 'atualizado_em'])

        subscription_data = data.get('subscription') or {}
        primeira_cobranca = _to_date(subscription_data.get('nextDueDate')) or timezone.localdate()
        assinatura, _ = AssinaturaAsaas.objects.update_or_create(
            checkout_origem=checkout,
            defaults={
                'perfil': checkout.perfil,
                'plano': checkout.plano,
                'ciclo': checkout.ciclo,
                'valor': checkout.valor,
                'asaas_customer_id': customer_id,
                'status': AssinaturaAsaas.Status.ACTIVE,
                'atual': True,
                'proximo_vencimento': primeira_cobranca,
                'acesso_ate': _add_cycle(primeira_cobranca, checkout.ciclo),
                'ultimo_payload': data,
            },
        )

        perfil = checkout.perfil
        perfil.plano_comercial = checkout.plano
        perfil.plano = checkout.plano.nivel_acesso
        perfil.plano_desejado_comercial = None
        perfil.plano_desejado = None
        perfil.inicio_acesso = perfil.inicio_acesso or timezone.localdate()
        perfil.fim_acesso = assinatura.acesso_ate
        perfil.renovacao_automatica = True
        perfil.ativo = True
        perfil.save(update_fields=[
            'plano_comercial', 'plano', 'plano_desejado_comercial', 'plano_desejado',
            'inicio_acesso', 'fim_acesso', 'renovacao_automatica', 'ativo', 'atualizado_em',
        ])
        return EventoWebhookAsaas.Status.PROCESSED, ''

    if event_type == 'CHECKOUT_CANCELED':
        checkout.status = AsaasCheckout.Status.CANCELED
    elif event_type == 'CHECKOUT_EXPIRED':
        checkout.status = AsaasCheckout.Status.EXPIRED
    elif event_type == 'CHECKOUT_CREATED':
        checkout.status = AsaasCheckout.Status.ACTIVE

    checkout.resposta_asaas = data
    checkout.save()
    return EventoWebhookAsaas.Status.PROCESSED, ''


@transaction.atomic
def _sincronizar_assinatura(event_type, payload):
    data = payload.get('subscription') or {}
    subscription_id = data.get('id') or ''
    customer_id = data.get('customer') or ''
    if not subscription_id:
        return EventoWebhookAsaas.Status.ERROR, 'Evento de assinatura sem subscription.id.'

    assinatura = _assinatura_por_ids(subscription_id, customer_id)
    if assinatura is None:
        return EventoWebhookAsaas.Status.PENDING, f'Assinatura {subscription_id} aguardando vínculo do Checkout.'

    assinatura.asaas_subscription_id = subscription_id
    assinatura.asaas_customer_id = customer_id or assinatura.asaas_customer_id
    assinatura.proximo_vencimento = _to_date(data.get('nextDueDate'))
    assinatura.ultimo_payload = data

    if event_type in {'SUBSCRIPTION_CREATED', 'SUBSCRIPTION_UPDATED'}:
        remote_status = (data.get('status') or '').upper()
        assinatura.status = AssinaturaAsaas.Status.ACTIVE if remote_status == 'ACTIVE' else AssinaturaAsaas.Status.INACTIVE
    elif event_type == 'SUBSCRIPTION_INACTIVATED':
        assinatura.status = AssinaturaAsaas.Status.INACTIVE
        assinatura.cancelamento_solicitado = True
        assinatura.perfil.renovacao_automatica = False
        assinatura.perfil.save(update_fields=['renovacao_automatica', 'atualizado_em'])
    elif event_type == 'SUBSCRIPTION_DELETED':
        assinatura.status = AssinaturaAsaas.Status.CANCELED
        assinatura.cancelamento_solicitado = True
        assinatura.encerrado_em = assinatura.encerrado_em or timezone.now()
        assinatura.perfil.renovacao_automatica = False
        assinatura.perfil.save(update_fields=['renovacao_automatica', 'atualizado_em'])

    assinatura.save()
    return EventoWebhookAsaas.Status.PROCESSED, ''


@transaction.atomic
def _sincronizar_pagamento(event_type, payload):
    data = payload.get('payment') or {}
    payment_id = data.get('id') or ''
    if not payment_id:
        return EventoWebhookAsaas.Status.ERROR, 'Evento de cobrança sem payment.id.'

    subscription_id = data.get('subscription') or ''
    customer_id = data.get('customer') or ''
    assinatura = _assinatura_por_ids(subscription_id, customer_id)

    pagamento, _ = PagamentoAsaas.objects.update_or_create(
        asaas_payment_id=payment_id,
        defaults={
            'assinatura': assinatura,
            'asaas_subscription_id': subscription_id,
            'asaas_customer_id': customer_id,
            'status': data.get('status') or event_type,
            'forma_pagamento': data.get('billingType') or '',
            'valor': _to_decimal(data.get('value')),
            'valor_liquido': _to_decimal(data.get('netValue')),
            'vencimento': _to_date(data.get('dueDate')),
            'confirmacao': _to_date(data.get('confirmedDate')),
            'pagamento': _to_date(data.get('paymentDate')),
            'invoice_url': data.get('invoiceUrl') or '',
            'ultimo_payload': data,
        },
    )

    if assinatura is None:
        return EventoWebhookAsaas.Status.PENDING, f'Pagamento {payment_id} aguardando vínculo da assinatura.'

    perfil = assinatura.perfil
    due_date = pagamento.vencimento or assinatura.proximo_vencimento or timezone.localdate()

    if event_type in {'PAYMENT_CONFIRMED', 'PAYMENT_RECEIVED'}:
        assinatura.status = AssinaturaAsaas.Status.ACTIVE
        assinatura.acesso_ate = _add_cycle(due_date, assinatura.ciclo)
        perfil.fim_acesso = assinatura.acesso_ate
        perfil.ativo = True
        perfil.renovacao_automatica = not assinatura.cancelamento_solicitado
        perfil.save(update_fields=['fim_acesso', 'ativo', 'renovacao_automatica', 'atualizado_em'])
    elif event_type in {'PAYMENT_OVERDUE', 'PAYMENT_CREDIT_CARD_CAPTURE_REFUSED'}:
        assinatura.status = AssinaturaAsaas.Status.PAST_DUE
        perfil.fim_acesso = max(perfil.fim_acesso or due_date, _grace_until(due_date))
        perfil.save(update_fields=['fim_acesso', 'atualizado_em'])
    elif event_type in {'PAYMENT_REFUNDED', 'PAYMENT_CHARGEBACK_REQUESTED', 'PAYMENT_REPROVED_BY_RISK_ANALYSIS'}:
        assinatura.status = AssinaturaAsaas.Status.SUSPENDED
        perfil.fim_acesso = timezone.localdate() - timedelta(days=1)
        perfil.save(update_fields=['fim_acesso', 'atualizado_em'])

    assinatura.ultimo_payload = data
    assinatura.save()
    return EventoWebhookAsaas.Status.PROCESSED, ''


def processar_evento(evento):
    payload = evento.payload
    event_type = evento.event_type
    evento.tentativas += 1

    try:
        if event_type.startswith('CHECKOUT_'):
            status, erro = _ativar_checkout(event_type, payload)
        elif event_type.startswith('SUBSCRIPTION_'):
            status, erro = _sincronizar_assinatura(event_type, payload)
        elif event_type.startswith('PAYMENT_'):
            status, erro = _sincronizar_pagamento(event_type, payload)
        else:
            status, erro = EventoWebhookAsaas.Status.IGNORED, ''
    except Exception as exc:
        evento.status = EventoWebhookAsaas.Status.ERROR
        evento.erro = str(exc)
        evento.save(update_fields=['status', 'erro', 'tentativas'])
        raise

    evento.status = status
    evento.erro = erro or ''
    evento.processado_em = timezone.now() if status in {
        EventoWebhookAsaas.Status.PROCESSED,
        EventoWebhookAsaas.Status.IGNORED,
    } else None
    evento.save(update_fields=['status', 'erro', 'tentativas', 'processado_em'])
    return evento
