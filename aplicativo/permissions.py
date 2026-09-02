from functools import wraps
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse

from aplicativo.access import resolver_acesso_aplicativo


SESSION_LOGOUT_LOCAL = 'aplicativo_logout_local'


def _redirect_login(request):
    # MÓDULO 2 — não propaga query strings da rota protegida para a URL de login.
    # Após autenticar, o usuário entra pela rota limpa /mapa/.
    return redirect(reverse('aplicativo:login'))


def cliente_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _redirect_login(request)

        # MÓDULO 2: administradores podem sair apenas do aplicativo sem encerrar
        # a sessão administrativa do /painel/. Nesse caso exigimos novo login
        # explícito antes de reabrir a área cliente.
        if request.session.get(SESSION_LOGOUT_LOCAL):
            return _redirect_login(request)

        acesso = resolver_acesso_aplicativo(request.user)
        if acesso is None:
            return HttpResponseForbidden('Esta conta não possui acesso à Área Aplicativo.')

        request.acesso_aplicativo = acesso
        return view_func(request, *args, **kwargs)

    return wrapper


def plano_ativo_required(view_func):
    @cliente_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.acesso_aplicativo.pode_consultar:
            return HttpResponseForbidden('É necessário possuir um plano ativo para realizar esta operação.')
        return view_func(request, *args, **kwargs)

    return wrapper


def plano_total_required(view_func):
    @cliente_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.acesso_aplicativo.pode_desenhar_glebas:
            return HttpResponseForbidden('Esta funcionalidade está disponível somente no plano Total.')
        return view_func(request, *args, **kwargs)

    return wrapper
