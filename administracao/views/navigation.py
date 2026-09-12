from django.shortcuts import render

from administracao.permissions import admin_required, commercial_manager_required


@admin_required
def central_dados(request):
    return render(request, 'administracao/navigation/dados.html')


@commercial_manager_required
def central_financeiro(request):
    return render(request, 'administracao/navigation/financeiro.html')


@admin_required
def central_comunicacao(request):
    return render(request, 'administracao/navigation/comunicacao.html')


@admin_required
def central_sistema(request):
    return render(request, 'administracao/navigation/sistema.html')
