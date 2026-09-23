"""Rotas do serviço Manage, sem endpoints de clientes ou pagamentos."""

from django.shortcuts import redirect
from django.urls import include, path

from administracao.views.health import health


urlpatterns = [
    path('health/', health, name='health'),
    path('painel/', include('administracao.urls')),
    path('', lambda request: redirect('administracao:dashboard')),
]
