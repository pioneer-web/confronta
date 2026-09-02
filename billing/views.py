import json
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from billing.models import AsaasCheckout, AssinaturaAsaas, EventoWebhookAsaas
from billing.services.asaas import AsaasAPIError, AsaasClient, AsaasConfigurationError
from billing.services.checkout import assinatura_atual, criar_checkout
from aplicativo.models import PerfilCliente


@login_required
@require_POST
def iniciar_checkout(request):
    perfil = getattr(request.user, 'perfil_cliente', None)
    if perfil is None:
        messages.info(request, 'Contas administrativas não precisam contratar um plano.')
        return redirect('aplicativo:inicio')

    ciclo = (request.POST.get('ciclo') or '').strip().upper()
    if ciclo not in {AsaasCheckout.Ciclo.MONTHLY, AsaasCheckout.Ciclo.YEARLY}:
        messages.error(request, 'Escolha uma modalidade de assinatura válida.')
        return redirect('aplicativo:planos')

    try:
        checkout = criar_checkout(request, perfil, ciclo)
    except (AsaasConfigurationError, AsaasAPIError, RuntimeError, ValueError) as exc:
        messages.error(request, 'Não foi possível iniciar o pagamento agora. Tente novamente em alguns instantes.')
        return redirect('aplicativo:planos')

    return redirect(checkout.checkout_url)


@login_required
@require_POST
def cancelar_assinatura(request):
    perfil = getattr(request.user, 'perfil_cliente', None)
    if perfil is None:
        return redirect('aplicativo:inicio')

    assinatura = assinatura_atual(perfil)
    if assinatura is None or not assinatura.asaas_subscription_id:
        messages.error(request, 'A assinatura ainda não está sincronizada com o Asaas.')
        return redirect('aplicativo:planos')

    try:
        AsaasClient.from_settings().remover_assinatura(assinatura.asaas_subscription_id)
    except (AsaasConfigurationError, AsaasAPIError) as exc:
        messages.error(request, f'Não foi possível cancelar a recorrência: {exc}')
        return redirect('aplicativo:planos')

    assinatura.status = AssinaturaAsaas.Status.CANCELED
    assinatura.cancelamento_solicitado = True
    assinatura.encerrado_em = timezone.now()
    assinatura.save(update_fields=['status', 'cancelamento_solicitado', 'encerrado_em', 'atualizado_em'])

    perfil.renovacao_automatica = False
    perfil.save(update_fields=['renovacao_automatica', 'atualizado_em'])
    messages.success(request, 'Renovação automática cancelada. O acesso já pago permanece até o fim da vigência atual.')
    return redirect('aplicativo:planos')


@require_GET
def checkout_sucesso(request):
    acesso_confirmado = False
    status_url = ''
    if request.user.is_authenticated:
        perfil = getattr(request.user, 'perfil_cliente', None)
        if perfil is not None:
            assinatura = assinatura_atual(perfil)
            acesso_confirmado = bool(
                assinatura
                and assinatura.status == AssinaturaAsaas.Status.ACTIVE
                and perfil.ativo
                and perfil.plano != PerfilCliente.Plano.SEM_PLANO
            )
            status_url = reverse('billing:status_assinatura')

    return render(request, 'billing/resultado.html', {
        'titulo': 'Acesso confirmado' if acesso_confirmado else 'Pagamento concluído',
        'mensagem': (
            'Seu pagamento foi confirmado pelo CONFRONTA. Sua assinatura está ativa e você já pode acessar o sistema.'
            if acesso_confirmado
            else 'Recebemos a conclusão do pagamento. O CONFRONTA está confirmando sua assinatura; normalmente isso leva apenas alguns segundos.'
        ),
        'estado': 'sucesso',
        'acesso_confirmado': acesso_confirmado,
        'status_url': status_url,
        'acesso_url': reverse('aplicativo:inicio'),
    })


@login_required
@require_GET
def status_assinatura(request):
    perfil = getattr(request.user, 'perfil_cliente', None)
    if perfil is None:
        return JsonResponse({
            'ok': True,
            'acesso_confirmado': True,
            'status': 'ADMIN',
            'mensagem': 'Conta administrativa com acesso liberado.',
            'redirect_url': reverse('aplicativo:inicio'),
        })

    assinatura = assinatura_atual(perfil)
    confirmado = bool(
        assinatura
        and assinatura.status == AssinaturaAsaas.Status.ACTIVE
        and perfil.ativo
        and perfil.plano != PerfilCliente.Plano.SEM_PLANO
    )

    if confirmado:
        mensagem = 'Pagamento confirmado pelo CONFRONTA. Seu acesso já está liberado.'
    elif assinatura is not None and assinatura.status == AssinaturaAsaas.Status.PAST_DUE:
        mensagem = 'A assinatura está aguardando regularização do pagamento.'
    elif assinatura is not None and assinatura.status in {
        AssinaturaAsaas.Status.SUSPENDED, AssinaturaAsaas.Status.INACTIVE, AssinaturaAsaas.Status.CANCELED,
    }:
        mensagem = 'A assinatura não está ativa. Consulte a área de assinatura para mais detalhes.'
    else:
        mensagem = 'O CONFRONTA ainda está confirmando sua assinatura.'

    return JsonResponse({
        'ok': True,
        'acesso_confirmado': confirmado,
        'status': assinatura.status if assinatura else 'PENDING',
        'mensagem': mensagem,
        'redirect_url': reverse('aplicativo:inicio') if confirmado else '',
    })


@require_GET
def checkout_cancelado(request):
    return render(request, 'billing/resultado.html', {
        'titulo': 'Pagamento cancelado',
        'mensagem': 'Nenhuma assinatura foi ativada por este retorno. Você pode voltar e tentar novamente.',
        'estado': 'cancelado',
    })


@require_GET
def checkout_expirado(request):
    return render(request, 'billing/resultado.html', {
        'titulo': 'Checkout expirado',
        'mensagem': 'O link de pagamento expirou. Volte aos planos para gerar um novo Checkout.',
        'estado': 'expirado',
    })


@csrf_exempt
@require_POST
def webhook_asaas(request):
    token_esperado = (getattr(settings, 'ASAAS_WEBHOOK_TOKEN', '') or '').strip()
    token_recebido = (request.headers.get('asaas-access-token') or '').strip()
    if not token_esperado:
        return HttpResponse('Webhook Asaas não configurado.', status=503)
    if not token_recebido or not secrets.compare_digest(token_recebido, token_esperado):
        return HttpResponseForbidden('Token inválido.')

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    event_id = str(payload.get('id') or '').strip()
    event_type = str(payload.get('event') or '').strip()
    if not event_id or not event_type:
        return JsonResponse({'ok': False, 'error': 'id/event obrigatórios'}, status=400)

    evento, created = EventoWebhookAsaas.objects.get_or_create(
        event_id=event_id,
        defaults={'event_type': event_type, 'payload': payload},
    )
    if not created:
        # idempotência: já persistimos este evento. Responder 200 evita reenvio desnecessário.
        return JsonResponse({'ok': True, 'duplicate': True})

    return JsonResponse({'ok': True, 'queued': True}, status=200)
