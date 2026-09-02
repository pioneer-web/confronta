from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render

from aplicativo.forms import ContaClienteForm
from aplicativo.permissions import cliente_required
from billing.services.checkout import assinatura_atual


@cliente_required
@transaction.atomic
def conta_view(request):
    """Permite ao usuário editar apenas os dados básicos da própria conta."""
    form = ContaClienteForm(request.POST or None, user=request.user)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Dados da conta atualizados com sucesso.')
        return redirect('aplicativo:conta')

    perfil = getattr(request.user, 'perfil_cliente', None)
    assinatura = assinatura_atual(perfil) if perfil else None
    return render(request, 'aplicativo/conta.html', {
        'form': form,
        'perfil_cliente': perfil,
        'acesso_aplicativo': request.acesso_aplicativo,
        'assinatura_asaas': assinatura,
    })
