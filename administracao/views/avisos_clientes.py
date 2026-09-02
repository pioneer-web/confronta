from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from administracao.forms import AvisoClienteAdminForm
from administracao.permissions import commercial_manager_required
from administracao.services.auditoria import registrar_auditoria
from aplicativo.models import AvisoCliente


@commercial_manager_required
def lista_avisos_clientes(request):
    if request.method == 'POST':
        form = AvisoClienteAdminForm(request.POST)
        if form.is_valid():
            aviso = form.save(commit=False)
            aviso.criado_por = request.user
            aviso.save()
            registrar_auditoria(
                request.user,
                'AVISO_CLIENTE_PUBLICADO',
                'AvisoCliente',
                aviso.pk,
                {'mensagem': aviso.mensagem},
            )
            messages.success(request, 'Aviso publicado para os clientes.')
            return redirect('administracao:avisos_clientes')
    else:
        form = AvisoClienteAdminForm()

    avisos = (
        AvisoCliente.objects
        .select_related('criado_por')
        .annotate(total_leituras=Count('leituras'))
        .order_by('-criado_em', '-id')[:100]
    )
    return render(request, 'administracao/avisos_clientes/lista.html', {
        'form': form,
        'avisos_clientes': avisos,
    })


@commercial_manager_required
@require_POST
def alternar_aviso_cliente(request, pk):
    aviso = get_object_or_404(AvisoCliente, pk=pk)
    aviso.ativo = not aviso.ativo
    aviso.save(update_fields=['ativo'])

    registrar_auditoria(
        request.user,
        'AVISO_CLIENTE_REATIVADO' if aviso.ativo else 'AVISO_CLIENTE_ENCERRADO',
        'AvisoCliente',
        aviso.pk,
        {'ativo': aviso.ativo},
    )

    if aviso.ativo:
        messages.success(request, 'Aviso reativado. Ele volta a aparecer aos clientes que ainda não o leram.')
    else:
        messages.success(request, 'Aviso encerrado. Ele não aparece mais no sino dos clientes.')
    return redirect('administracao:avisos_clientes')
