from datetime import timedelta
from decimal import Decimal
import logging
from urllib.parse import urlparse

from django.conf import settings
from django.db import models
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from aplicativo.models import PlanoComercial
from aplicativo.models import PerfilCliente
from billing.models import AsaasCheckout, AssinaturaAsaas
from billing.services.asaas import AsaasAPIError, AsaasClient
from billing.services.cupons import CupomInvalido, calcular_cupom, liberar_cupom, reservar_cupom
from billing.services.sanitizacao import sanitizar_resposta


logger = logging.getLogger(__name__)


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


def _liberar_checkouts_expirados(perfil):
    expirados = AsaasCheckout.objects.filter(
        perfil=perfil,
        status=AsaasCheckout.Status.ACTIVE,
        expira_em__lte=timezone.now(),
    )
    for checkout in expirados:
        checkout.status = AsaasCheckout.Status.EXPIRED
        checkout.save(update_fields=['status', 'atualizado_em'])
        liberar_cupom(checkout)


def _resolver_checkout_aberto(perfil, ciclo, client, modalidade=None, renovacao_de=None, cupom=''):
    aberto = _checkout_aberto(perfil)
    if renovacao_de:
        aberto = AsaasCheckout.objects.filter(
            renovacao_de=renovacao_de,
            status__in=[AsaasCheckout.Status.CREATING, AsaasCheckout.Status.ACTIVE],
        ).filter(models.Q(expira_em__isnull=True) | models.Q(expira_em__gt=timezone.now())).order_by('-criado_em').first()
    elif aberto and aberto.renovacao_de_id:
        aberto = None
    if aberto is None:
        return None

    if renovacao_de and aberto.status == AsaasCheckout.Status.ACTIVE and aberto.checkout_url:
        return aberto

    if (
        aberto.ciclo == ciclo
        and aberto.modalidade == modalidade
        and (aberto.cupom or '').upper() == (cupom or '').strip().upper()
        and aberto.status == AsaasCheckout.Status.ACTIVE
        and aberto.checkout_url
    ):
        # Duplo clique/reenvio do formulário: reutiliza o mesmo Checkout em vez
        # de criar uma segunda assinatura potencial para o mesmo cliente.
        return aberto

    if aberto.asaas_checkout_id:
        try:
            client.cancelar_checkout(aberto.asaas_checkout_id)
        except AsaasAPIError as exc:
            # O Asaas pode já ter encerrado o checkout remoto. Só aceitamos
            # esse 400 quando o código estruturado e a descrição confirmam isso.
            encerrado = exc.status_code == 404
            if exc.status_code == 400 and isinstance(exc.response, dict):
                errors = exc.response.get('errors')
                encerrado = isinstance(errors, list) and any(
                    isinstance(error, dict)
                    and error.get('code') == 'invalid_action'
                    and 'checkout não está ativo para ser cancelado' in str(error.get('description') or '').casefold()
                    for error in errors
                )
            if not encerrado:
                raise RuntimeError('Existe um Checkout anterior ainda aberto. Tente novamente em instantes.') from exc
            logger.info('Checkout remoto indisponível para cancelamento; encerrando registro local.')

    aberto.status = AsaasCheckout.Status.CANCELED
    aberto.save(update_fields=['status', 'atualizado_em'])
    liberar_cupom(aberto)
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






@transaction.atomic
def criar_checkout(request, perfil, ciclo, modalidade=None, parcelas_maximas_ofertadas=None, cupom='', renovacao_de=None):
    PerfilCliente.objects.select_for_update().get(pk=perfil.pk)
    plano = plano_confronta_ativo()
    if plano is None:
        raise RuntimeError('Nenhum plano CONFRONTA ativo está configurado.')

    if ciclo not in {AsaasCheckout.Ciclo.MONTHLY, AsaasCheckout.Ciclo.YEARLY}:
        raise ValueError('Ciclo de cobrança inválido.')

    if ciclo == AsaasCheckout.Ciclo.MONTHLY:
        modalidade, parcelas_maximas_ofertadas = AsaasCheckout.Modalidade.MONTHLY, 1
    else:
        modalidade = modalidade or AsaasCheckout.Modalidade.YEARLY_INSTALLMENT
        parcelas_maximas_ofertadas = int(parcelas_maximas_ofertadas or (1 if modalidade == AsaasCheckout.Modalidade.YEARLY_CASH else PlanoComercial.PARCELAS_ANUAL))
        if modalidade == AsaasCheckout.Modalidade.YEARLY_CASH:
            parcelas_maximas_ofertadas = 1
        elif modalidade != AsaasCheckout.Modalidade.YEARLY_INSTALLMENT or not 2 <= parcelas_maximas_ofertadas <= PlanoComercial.PARCELAS_ANUAL:
            raise ValueError('O anual parcelado permite de 2 a 6 parcelas.')

    if renovacao_de is None and not pode_criar_checkout(perfil):
        raise RuntimeError('Já existe uma assinatura em andamento para esta conta.')
    if renovacao_de is not None and (renovacao_de.perfil_id != perfil.pk or renovacao_de.modalidade != AsaasCheckout.Modalidade.YEARLY_INSTALLMENT):
        raise ValueError('Esta assinatura não permite renovação por Checkout.')

    valor = valor_do_ciclo(plano, ciclo)
    cupom_obj = None
    desconto = Decimal('0.00')
    valor_final = valor
    if (cupom or '').strip():
        cupom_obj, desconto, valor_final = calcular_cupom(cupom, ciclo, valor, lock=True)
        if modalidade != AsaasCheckout.Modalidade.YEARLY_INSTALLMENT:
            raise CupomInvalido('Cupom promocional não pode reduzir cobranças recorrentes; selecione o anual parcelado.')
        if valor_final <= 0:
            raise CupomInvalido('O desconto precisa deixar um valor positivo para cobrança.')

    _liberar_checkouts_expirados(perfil)
    client = AsaasClient.from_settings()
    checkout_existente = _resolver_checkout_aberto(perfil, ciclo, client, modalidade, renovacao_de, cupom)
    if checkout_existente is not None:
        return checkout_existente

    agora = timezone.localtime()
    minutos = int(getattr(settings, 'ASAAS_CHECKOUT_EXPIRES_MINUTES', 60))
    callbacks = {
        'successUrl': _url_callback_publica(request, 'billing:checkout_sucesso'),
        'cancelUrl': _url_callback_publica(request, 'billing:checkout_cancelado'),
        'expiredUrl': _url_callback_publica(request, 'billing:checkout_expirado'),
    }
    checkout = AsaasCheckout.objects.create(
        usuario=perfil.usuario,
        perfil=perfil,
        plano=plano,
        renovacao_de=renovacao_de,
        ciclo=ciclo,
        modalidade=modalidade,
        parcelas_maximas_ofertadas=parcelas_maximas_ofertadas,
        parcelas_contratadas=None,
        valor=valor_final,
        valor_original=valor,
        valor_desconto=desconto,
        desconto=desconto,
        valor_final=valor_final,
        cupom=cupom_obj.codigo if cupom_obj else '',
        status=AsaasCheckout.Status.CREATING,
    )
    if cupom_obj:
        reservar_cupom(checkout, cupom_obj.codigo)

    payload = {
        'billingTypes': ['CREDIT_CARD'],
        'minutesToExpire': minutos,
        'externalReference': f'confronta:{checkout.referencia}',
        'callback': callbacks,
        'items': [{
            'name': (
                'CONFRONTA Mensal'
                if ciclo == AsaasCheckout.Ciclo.MONTHLY
                else 'CONFRONTA Anual'
            ),
            'description': (
                'Assinatura mensal do CONFRONTA — Inteligência Territorial'
                if ciclo == AsaasCheckout.Ciclo.MONTHLY
                else 'Acesso anual ao CONFRONTA — Inteligência Territorial'
            ),
            'quantity': 1,
            'value': float(valor_final),
        }],
    }

    if ciclo == AsaasCheckout.Ciclo.MONTHLY:
        # Plano mensal: assinatura com renovação automática.
        payload['chargeTypes'] = ['RECURRENT']
        payload['subscription'] = {
            'cycle': AsaasCheckout.Ciclo.MONTHLY,
            'nextDueDate': agora.strftime('%Y-%m-%d %H:%M:%S'),
        }
    else:
        if modalidade == AsaasCheckout.Modalidade.YEARLY_CASH:
            payload['chargeTypes'] = ['RECURRENT']
            payload['subscription'] = {'cycle': 'YEARLY', 'nextDueDate': agora.strftime('%Y-%m-%d')}
        else:
            payload['chargeTypes'] = ['DETACHED', 'INSTALLMENT']
            payload['installment'] = {'maxInstallmentCount': parcelas_maximas_ofertadas}

    # Não enviamos `customerData` nesta V1. O Asaas exige o conjunto cadastral
    # completo quando esse objeto é informado (incluindo CPF/CNPJ e endereço).
    # Como o CONFRONTA não armazena esses dados, deixamos o Checkout hospedado
    # coletá-los diretamente do pagador. Isso evita duplicar dados sensíveis e
    # mantém o cadastro financeiro sob responsabilidade do gateway.

    try:
        response = client.criar_checkout(payload)
    except Exception as exc:
        checkout.status = AsaasCheckout.Status.ERROR
        checkout.erro = 'Falha ao criar Checkout no Asaas.'
        if hasattr(exc, 'response'):
            checkout.resposta_asaas = sanitizar_resposta(exc.response)
        checkout.save(update_fields=['status', 'erro', 'resposta_asaas', 'atualizado_em'])
        liberar_cupom(checkout)
        raise

    checkout_id = response.get('id')
    checkout_url = response.get('link') or ''
    if not checkout_id:
        checkout.status = AsaasCheckout.Status.ERROR
        checkout.resposta_asaas = sanitizar_resposta(response)
        checkout.erro = 'Resposta do Asaas sem identificador do Checkout.'
        checkout.save(update_fields=['status', 'resposta_asaas', 'erro', 'atualizado_em'])
        liberar_cupom(checkout)
        raise RuntimeError(checkout.erro)

    if not checkout_url:
        host_checkout = (
            'https://sandbox.asaas.com'
            if getattr(settings, 'ASAAS_ENVIRONMENT', 'sandbox').lower() == 'sandbox'
            else 'https://asaas.com'
        )
        checkout_url = f'{host_checkout}/checkoutSession/show/{checkout_id}'

    checkout.asaas_checkout_id = checkout_id
    parcelas_contratadas = response.get('installmentCount')
    try:
        parcelas_contratadas = int(parcelas_contratadas)
    except (TypeError, ValueError):
        parcelas_contratadas = None
    if parcelas_contratadas is not None and 1 <= parcelas_contratadas <= checkout.parcelas_maximas_ofertadas:
        checkout.parcelas_contratadas = parcelas_contratadas
    checkout.checkout_url = checkout_url
    checkout.status = AsaasCheckout.Status.ACTIVE
    checkout.resposta_asaas = sanitizar_resposta(response)
    checkout.expira_em = timezone.now() + timedelta(minutes=minutos)
    checkout.save(update_fields=[
        'asaas_checkout_id', 'checkout_url', 'status', 'resposta_asaas', 'parcelas_contratadas',
        'expira_em', 'atualizado_em',
    ])
    return checkout


@transaction.atomic
def criar_checkout_renovacao(request, perfil, assinatura, cupom=''):
    assinatura = AssinaturaAsaas.objects.select_for_update().get(pk=assinatura.pk)
    if assinatura.perfil_id != perfil.pk or assinatura.modalidade != AsaasCheckout.Modalidade.YEARLY_INSTALLMENT:
        raise ValueError('Esta assinatura não permite renovação por reautorização.')
    if assinatura.renovacao_status not in {
        AssinaturaAsaas.RenovacaoStatus.PENDING_REAUTHORIZATION,
        AssinaturaAsaas.RenovacaoStatus.CHECKOUT_CREATED,
    }:
        raise ValueError('A renovação ainda não está disponível.')
    limite_anterior = assinatura.parcelas_maximas_ofertadas or PlanoComercial.PARCELAS_ANUAL
    limite_atual = min(PlanoComercial.PARCELAS_ANUAL, max(2, limite_anterior))
    checkout = criar_checkout(
        request,
        perfil,
        AsaasCheckout.Ciclo.YEARLY,
        AsaasCheckout.Modalidade.YEARLY_INSTALLMENT,
        limite_atual,
        cupom,
        renovacao_de=assinatura,
    )
    assinatura.renovacao_status = AssinaturaAsaas.RenovacaoStatus.CHECKOUT_CREATED
    assinatura.save(update_fields=['renovacao_status', 'atualizado_em'])
    return checkout
