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
from billing.services.checkout import assinatura_atual, criar_checkout, criar_checkout_renovacao, plano_confronta_ativo, valor_do_ciclo
from billing.services.cupons import CupomInvalido, calcular_cupom, liberar_cupom
from billing.services.sanitizacao import sanitizar_evento
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
        modalidade = (request.POST.get('modalidade') or '').strip().upper() or None
        parcelas_maximas = request.POST.get('parcelas_maximas_ofertadas') or None
        checkout = criar_checkout(request, perfil, ciclo, modalidade, parcelas_maximas, request.POST.get('codigo_cupom', ''))
    except CupomInvalido as exc:
        messages.error(request, str(exc))
        return redirect('aplicativo:planos')
    except (AsaasConfigurationError, AsaasAPIError, RuntimeError, ValueError) as exc:
        messages.error(
            request,
            'Não foi possível iniciar o pagamento agora. Tente novamente em alguns instantes.'
        )

        if perfil.plano == PerfilCliente.Plano.SEM_PLANO:
            return redirect('aplicativo:cadastro_concluido')

        return redirect('aplicativo:planos')

    return redirect(checkout.checkout_url)


@login_required
@require_POST
def cancelar_assinatura(request):
    perfil = getattr(request.user, 'perfil_cliente', None)
    if perfil is None:
        return redirect('aplicativo:inicio')

    assinatura = assinatura_atual(perfil)
    if assinatura is None:
        messages.error(request, 'A assinatura ainda não está sincronizada com o Asaas.')
        return redirect('aplicativo:planos')

    if assinatura.modalidade == AsaasCheckout.Modalidade.YEARLY_INSTALLMENT:
        renovacoes_abertas = AsaasCheckout.objects.filter(
            renovacao_de=assinatura,
            status__in=[AsaasCheckout.Status.CREATING, AsaasCheckout.Status.ACTIVE],
        )
        renovacoes_abertas = list(renovacoes_abertas)
        if any(checkout.asaas_checkout_id for checkout in renovacoes_abertas):
            try:
                client = AsaasClient.from_settings()
                for checkout_aberto in renovacoes_abertas:
                    if not checkout_aberto.asaas_checkout_id:
                        continue
                    try:
                        client.cancelar_checkout(checkout_aberto.asaas_checkout_id)
                    except AsaasAPIError as exc:
                        if exc.status_code != 404:
                            raise
            except (AsaasConfigurationError, AsaasAPIError):
                messages.error(request, 'Não foi possível cancelar o Checkout de renovação no Asaas. Tente novamente.')
                return redirect('aplicativo:planos')
        for checkout_aberto in renovacoes_abertas:
            checkout_aberto.status = AsaasCheckout.Status.CANCELED
            checkout_aberto.save(update_fields=['status', 'atualizado_em'])
            liberar_cupom(checkout_aberto)
        assinatura.renovacao_status = AssinaturaAsaas.RenovacaoStatus.CANCELED
        assinatura.renovacao_pendente = False
        assinatura.cancelamento_solicitado = True
        assinatura.status = AssinaturaAsaas.Status.CANCELED
        assinatura.save(update_fields=['renovacao_status', 'renovacao_pendente', 'cancelamento_solicitado', 'status', 'atualizado_em'])
        perfil.renovacao_automatica = False
        perfil.save(update_fields=['renovacao_automatica', 'atualizado_em'])
        messages.success(request, 'A renovação futura foi cancelada. As parcelas da compra atual não foram alteradas e o acesso permanece até o fim da vigência.')
        return redirect('aplicativo:planos')

    if not assinatura.asaas_subscription_id:
        messages.error(request, 'A assinatura ainda não foi vinculada no Asaas. Tente novamente após a sincronização do webhook.')
        return redirect('aplicativo:planos')

    if assinatura.asaas_subscription_id:
        try:
            AsaasClient.from_settings().remover_assinatura(assinatura.asaas_subscription_id)
        except (AsaasConfigurationError, AsaasAPIError):
            messages.error(request, 'Não foi possível cancelar a renovação no Asaas. Tente novamente.')
            return redirect('aplicativo:planos')

    assinatura.renovacao_status = AssinaturaAsaas.RenovacaoStatus.CANCELED
    assinatura.cancelamento_solicitado = True
    assinatura.encerrado_em = assinatura.encerrado_em or timezone.now()
    assinatura.save(update_fields=['renovacao_status', 'cancelamento_solicitado', 'encerrado_em', 'atualizado_em'])

    perfil.renovacao_automatica = False
    perfil.save(update_fields=['renovacao_automatica', 'atualizado_em'])
    messages.success(request, 'Renovação automática cancelada. O acesso já pago permanece até o fim da vigência atual.')
    return redirect('aplicativo:planos')


@login_required
@require_POST
def renovar_anual(request):
    perfil = getattr(request.user, 'perfil_cliente', None)
    assinatura = assinatura_atual(perfil) if perfil else None
    if assinatura is None:
        messages.error(request, 'Não encontramos uma assinatura anual para renovar.')
        return redirect('aplicativo:planos')
    try:
        checkout = criar_checkout_renovacao(request, perfil, assinatura, request.POST.get('codigo_cupom', ''))
    except CupomInvalido as exc:
        messages.error(request, str(exc))
        return redirect('aplicativo:planos')
    except (AsaasConfigurationError, AsaasAPIError, RuntimeError, ValueError):
        messages.error(request, 'Não foi possível preparar a renovação. Tente novamente.')
        return redirect('aplicativo:planos')
    return redirect(checkout.checkout_url)


@login_required
@require_POST
def validar_cupom(request):
    ciclo = (request.POST.get('ciclo') or '').strip().upper()
    modalidade = (request.POST.get('modalidade') or '').strip().upper()
    codigo = request.POST.get('codigo_cupom', '')
    if ciclo not in {AsaasCheckout.Ciclo.MONTHLY, AsaasCheckout.Ciclo.YEARLY}:
        return JsonResponse({'ok': False, 'mensagem': 'Selecione um plano válido.'}, status=400)
    if ciclo != AsaasCheckout.Ciclo.YEARLY or modalidade != AsaasCheckout.Modalidade.YEARLY_INSTALLMENT:
        return JsonResponse({'ok': False, 'mensagem': 'Cupons promocionais só podem ser usados em anual parcelado, para não reduzir renovações automáticas.'}, status=400)
    if not codigo.strip():
        return JsonResponse({'ok': False, 'mensagem': 'Informe um código de cupom.'}, status=400)
    plano = plano_confronta_ativo()
    if plano is None:
        return JsonResponse({'ok': False, 'mensagem': 'Plano indisponível.'}, status=400)
    try:
        _, desconto, final = calcular_cupom(codigo, ciclo, valor_do_ciclo(plano, ciclo))
    except CupomInvalido as exc:
        return JsonResponse({'ok': False, 'mensagem': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'codigo': codigo.strip().upper(), 'desconto': f'{desconto:.2f}', 'valor_final': f'{final:.2f}'})


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
                and assinatura.status in {AssinaturaAsaas.Status.ACTIVE, AssinaturaAsaas.Status.CANCELED, AssinaturaAsaas.Status.INACTIVE}
                and perfil.ativo
                and (perfil.fim_acesso is None or perfil.fim_acesso >= timezone.localdate())
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
        and assinatura.status in {AssinaturaAsaas.Status.ACTIVE, AssinaturaAsaas.Status.CANCELED, AssinaturaAsaas.Status.INACTIVE}
        and perfil.ativo
        and (perfil.fim_acesso is None or perfil.fim_acesso >= timezone.localdate())
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
        defaults={'event_type': event_type, 'payload': sanitizar_evento(payload)},
    )
    if not created:
        # idempotência: já persistimos este evento. Responder 200 evita reenvio desnecessário.
        return JsonResponse({'ok': True, 'duplicate': True})

    return JsonResponse({'ok': True, 'queued': True}, status=200)
