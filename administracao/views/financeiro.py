from django.db.models import Count, Sum
from django.shortcuts import render

from administracao.permissions import commercial_manager_required
from billing.models import AssinaturaAsaas, EventoWebhookAsaas, PagamentoAsaas


@commercial_manager_required
def financeiro_asaas(request):
    assinaturas = (
        AssinaturaAsaas.objects
        .select_related('perfil__usuario', 'plano')
        .filter(atual=True)
        .order_by('-atualizado_em')
    )
    pagamentos = (
        PagamentoAsaas.objects
        .select_related('assinatura__perfil__usuario')
        .order_by('-atualizado_em')[:30]
    )
    eventos_erro = EventoWebhookAsaas.objects.filter(
        status__in=[EventoWebhookAsaas.Status.ERROR, EventoWebhookAsaas.Status.PENDING]
    ).order_by('-recebido_em')[:20]

    context = {
        'assinaturas': assinaturas[:100],
        'pagamentos': pagamentos,
        'eventos_erro': eventos_erro,
        'total_assinaturas': assinaturas.count(),
        'ativas': assinaturas.filter(status=AssinaturaAsaas.Status.ACTIVE).count(),
        'em_atraso': assinaturas.filter(status=AssinaturaAsaas.Status.PAST_DUE).count(),
        'canceladas': assinaturas.filter(status=AssinaturaAsaas.Status.CANCELED).count(),
    }
    return render(request, 'administracao/financeiro/asaas.html', context)
