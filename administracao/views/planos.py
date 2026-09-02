from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from administracao.forms import PlanoComercialForm
from administracao.permissions import commercial_manager_required
from administracao.services.auditoria import registrar_auditoria
from aplicativo.models import PerfilCliente, PlanoComercial


@commercial_manager_required
def lista_planos(request):
    planos = PlanoComercial.objects.all().order_by('ordem', 'preco_mensal', 'nome')
    return render(request, 'administracao/planos/lista.html', {
        'planos': planos,
        'total_planos': planos.count(),
        'ativos': planos.filter(ativo=True).count(),
        'destaques': planos.filter(ativo=True, destaque=True).count(),
    })


@commercial_manager_required
def novo_plano(request):
    form = PlanoComercialForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        plano = form.save()
        registrar_auditoria(
            request.user,
            'PLANO_CRIADO',
            'PlanoComercial',
            plano.pk,
            {'nome': plano.nome, 'slug': plano.slug, 'ativo': plano.ativo},
        )
        messages.success(request, 'Plano criado e publicado na Home.' if plano.ativo else 'Plano criado como inativo.')
        return redirect('administracao:planos')
    return render(request, 'administracao/planos/form.html', {
        'form': form,
        'titulo': 'Novo plano',
        'plano': None,
    })


@commercial_manager_required
def editar_plano(request, pk):
    plano = get_object_or_404(PlanoComercial, pk=pk)
    form = PlanoComercialForm(request.POST or None, instance=plano)
    if request.method == 'POST' and form.is_valid():
        plano = form.save()
        PerfilCliente.objects.filter(plano_comercial=plano).update(plano=plano.nivel_acesso)
        registrar_auditoria(
            request.user,
            'PLANO_ATUALIZADO',
            'PlanoComercial',
            plano.pk,
            {'nome': plano.nome, 'slug': plano.slug, 'ativo': plano.ativo},
        )
        messages.success(request, 'Plano atualizado. A Home já usa os dados cadastrados aqui.')
        return redirect('administracao:planos')
    return render(request, 'administracao/planos/form.html', {
        'form': form,
        'titulo': 'Editar plano',
        'plano': plano,
    })


@commercial_manager_required
def alternar_plano(request, pk):
    plano = get_object_or_404(PlanoComercial, pk=pk)
    if request.method != 'POST':
        return redirect('administracao:planos')
    plano.ativo = not plano.ativo
    plano.save(update_fields=['ativo', 'atualizado_em'])
    registrar_auditoria(
        request.user,
        'PLANO_STATUS_ALTERADO',
        'PlanoComercial',
        plano.pk,
        {'nome': plano.nome, 'ativo': plano.ativo},
    )
    messages.success(request, f'Plano {"publicado" if plano.ativo else "retirado da Home"}.')
    return redirect('administracao:planos')
