from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from aplicativo.models import PlanoComercial
from billing.models import AsaasCheckout, AssinaturaAsaas
from billing.services.asaas import AsaasAPIError, AsaasClient


def plano_confronta_ativo():
    return (
        PlanoComercial.objects
        .filter(ativo=True, slug='confronta', nivel_acesso=PlanoComercial.NivelAcesso.TOTAL)
        .order_by('ordem', 'pk')
        .first()
    )


def valor_do_ciclo(plano, ciclo):
    if ciclo == AsaasCheckout.Ciclo.MONTHLY:
        return plano.preco_mensal
    if ciclo == AsaasCheckout.Ciclo.YEARLY:
        return plano.preco_anual
    raise ValueError('Ciclo de cobrança inválido.')


def assinatura_atual(perfil):
    return (
        AssinaturaAsaas.objects
        .filter(perfil=perfil, atual=True)
        .order_by('-criado_em')
        .first()
    )


def pode_criar_checkout(perfil):
    assinatura = assinatura_atual(perfil)
    if not assinatura:
        return True
    return assinatura.status not in {
        AssinaturaAsaas.Status.ACTIVE,
        AssinaturaAsaas.Status.PENDING,
        AssinaturaAsaas.Status.PAST_DUE,
    }


def _checkout_aberto(perfil):
    agora = timezone.now()
    return (
        AsaasCheckout.objects
        .filter(
            perfil=perfil,
            status__in=[AsaasCheckout.Status.CREATING, AsaasCheckout.Status.ACTIVE],
        )
        .filter(models.Q(expira_em__isnull=True) | models.Q(expira_em__gt=agora))
        .order_by('-criado_em')
        .first()
    )


def _resolver_checkout_aberto(perfil, ciclo, client):
    aberto = _checkout_aberto(perfil)
    if aberto is None:
        return None

    if aberto.ciclo == ciclo and aberto.status == AsaasCheckout.Status.ACTIVE and aberto.checkout_url:
        # Duplo clique/reenvio do formulário: reutiliza o mesmo Checkout em vez
        # de criar uma segunda assinatura potencial para o mesmo cliente.
        return aberto

    if aberto.asaas_checkout_id:
        try:
            client.cancelar_checkout(aberto.asaas_checkout_id)
        except AsaasAPIError as exc:
            # Se o Checkout já não existe/expirou no Asaas, podemos substituí-lo.
            if exc.status_code != 404:
                raise RuntimeError('Existe um Checkout anterior ainda aberto. Tente novamente em instantes.') from exc

    aberto.status = AsaasCheckout.Status.CANCELED
    aberto.save(update_fields=['status', 'atualizado_em'])
    return None


def _url_callback_publica(request, route_name):
    path = reverse(route_name)
    base = (getattr(settings, 'ASAAS_CALLBACK_BASE_URL', '') or '').strip().rstrip('/')
    url = f'{base}{path}' if base else request.build_absolute_uri(path)

    parsed = urlparse(url)
    hostname = (parsed.hostname or '').lower()
    local_hosts = {'localhost', '127.0.0.1', '0.0.0.0', '::1'}

    # O Checkout do Asaas rejeita callbacks locais. Além disso, para a
    # jornada financeira usamos HTTPS mesmo quando o CONFRONTA local roda HTTP.
    if parsed.scheme != 'https' or hostname in local_hosts or not hostname:
        raise RuntimeError(
            'O Asaas exige URLs públicas HTTPS para successUrl/cancelUrl/expiredUrl. '
            'Configure ASAAS_CALLBACK_BASE_URL com a URL HTTPS pública do CONFRONTA '
            '(em Sandbox local, use um túnel HTTPS temporário).'
        )
    return url






def criar_checkout(request, perfil, ciclo):
    plano = plano_confronta_ativo()
    if plano is None:
        raise RuntimeError('Nenhum plano CONFRONTA ativo está configurado.')

    if ciclo not in {AsaasCheckout.Ciclo.MONTHLY, AsaasCheckout.Ciclo.YEARLY}:
        raise ValueError('Ciclo de cobrança inválido.')

    if not pode_criar_checkout(perfil):
        raise RuntimeError('Já existe uma assinatura em andamento para esta conta.')

    client = AsaasClient.from_settings()
    checkout_existente = _resolver_checkout_aberto(perfil, ciclo, client)
    if checkout_existente is not None:
        return checkout_existente

    valor = valor_do_ciclo(plano, ciclo)
    checkout = AsaasCheckout.objects.create(
        usuario=perfil.usuario,
        perfil=perfil,
        plano=plano,
        ciclo=ciclo,
        valor=valor,
        status=AsaasCheckout.Status.CREATING,
    )

    agora = timezone.localtime()
    minutos = int(getattr(settings, 'ASAAS_CHECKOUT_EXPIRES_MINUTES', 60))
    callbacks = {
        'successUrl': _url_callback_publica(request, 'billing:checkout_sucesso'),
        'cancelUrl': _url_callback_publica(request, 'billing:checkout_cancelado'),
        'expiredUrl': _url_callback_publica(request, 'billing:checkout_expirado'),
    }

    payload = {
        'billingTypes': ['CREDIT_CARD'],
        'chargeTypes': ['RECURRENT'],
        'minutesToExpire': minutos,
        'externalReference': f'confronta:{checkout.referencia}',
        'callback': callbacks,
        'items': [{
            'name': 'CONFRONTA Mensal' if ciclo == AsaasCheckout.Ciclo.MONTHLY else 'CONFRONTA Anual',
            'description': 'Assinatura de acesso ao CONFRONTA — Inteligência Territorial',
            'quantity': 1,
            'value': float(valor),
        }],
        'subscription': {
            'cycle': ciclo,
            'nextDueDate': agora.strftime('%Y-%m-%d %H:%M:%S'),
        },
    }

    # Não enviamos `customerData` nesta V1. O Asaas exige o conjunto cadastral
    # completo quando esse objeto é informado (incluindo CPF/CNPJ e endereço).
    # Como o CONFRONTA não armazena esses dados, deixamos o Checkout hospedado
    # coletá-los diretamente do pagador. Isso evita duplicar dados sensíveis e
    # mantém o cadastro financeiro sob responsabilidade do gateway.

    try:
        response = client.criar_checkout(payload)
    except Exception as exc:
        checkout.status = AsaasCheckout.Status.ERROR
        checkout.erro = str(exc)
        if hasattr(exc, 'response'):
            checkout.resposta_asaas = exc.response
        checkout.save(update_fields=['status', 'erro', 'resposta_asaas', 'atualizado_em'])
        raise

    checkout_id = response.get('id')
    checkout_url = response.get('link') or ''
    if not checkout_id:
        checkout.status = AsaasCheckout.Status.ERROR
        checkout.resposta_asaas = response
        checkout.erro = 'Resposta do Asaas sem identificador do Checkout.'
        checkout.save(update_fields=['status', 'resposta_asaas', 'erro', 'atualizado_em'])
        raise RuntimeError(checkout.erro)

    if not checkout_url:
        host_checkout = (
            'https://sandbox.asaas.com'
            if getattr(settings, 'ASAAS_ENVIRONMENT', 'sandbox').lower() == 'sandbox'
            else 'https://asaas.com'
        )
        checkout_url = f'{host_checkout}/checkoutSession/show/{checkout_id}'

    checkout.asaas_checkout_id = checkout_id
    checkout.checkout_url = checkout_url
    checkout.status = AsaasCheckout.Status.ACTIVE
    checkout.resposta_asaas = response
    checkout.expira_em = timezone.now() + timedelta(minutes=minutos)
    checkout.save(update_fields=[
        'asaas_checkout_id', 'checkout_url', 'status', 'resposta_asaas',
        'expira_em', 'atualizado_em',
    ])
    return checkout
