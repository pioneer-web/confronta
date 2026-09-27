from django.shortcuts import render

from administracao.permissions import admin_required, commercial_manager_required
from administracao.source_catalog import SOURCE_PROFILES


@admin_required
def central_dados(request):
    # Renderiza um card por perfil do catálogo. Não agrupa por organization,
    # FonteDados/enum ou schema: SICOR e Operações SICOR são fontes distintas.
    source_cards = [
        source for source in SOURCE_PROFILES
        if source.is_importable and source.implementation != 'FUTURO'
    ]
    return render(
        request,
        'administracao/navigation/dados.html',
        {'manage_sidebar_sources': source_cards},
    )


@commercial_manager_required
def central_financeiro(request):
    return render(request, 'administracao/navigation/financeiro.html')


@admin_required
def central_comunicacao(request):
    return render(request, 'administracao/navigation/comunicacao.html')


@admin_required
def central_sistema(request):
    return render(request, 'administracao/navigation/sistema.html')
