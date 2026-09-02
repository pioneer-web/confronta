from django.shortcuts import render

from aplicativo.models import PlanoComercial
from aplicativo.permissions import cliente_required
from billing.services.checkout import assinatura_atual


@cliente_required
def planos_view(request):
    acesso = request.acesso_aplicativo
    perfil = request.user.perfil_cliente if acesso.origem == 'CLIENTE' else None
    plano = PlanoComercial.objects.filter(slug='confronta', ativo=True).first()
    assinatura = assinatura_atual(perfil) if perfil else None
    pagamentos = assinatura.pagamentos.all()[:8] if assinatura else []

    return render(request, 'aplicativo/planos.html', {
        'perfil_cliente': perfil,
        'acesso_aplicativo': acesso,
        'plano_confronta': plano,
        'assinatura_asaas': assinatura,
        'pagamentos_asaas': pagamentos,
    })
