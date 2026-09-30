from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from administracao.permissions import commercial_manager_required
from billing.forms import CupomForm
from billing.models import Cupom


@commercial_manager_required
def lista_cupons(request):
    return render(request, 'administracao/cupons/lista.html', {'cupons': Cupom.objects.all()})


@commercial_manager_required
@require_http_methods(['GET', 'POST'])
def editar_cupom(request, pk=None):
    cupom = get_object_or_404(Cupom, pk=pk) if pk else None
    form = CupomForm(request.POST or None, instance=cupom)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Cupom salvo.')
        return redirect('administracao:cupons')
    return render(request, 'administracao/cupons/form.html', {'form': form, 'cupom': cupom})
