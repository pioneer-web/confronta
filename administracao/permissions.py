from functools import wraps
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import redirect


ADMIN_LOGIN_NEXT_SESSION_KEY = 'administracao_login_next'


def _clean_login_redirect(request):
    if request.user.is_authenticated:
        return None
    request.session[ADMIN_LOGIN_NEXT_SESSION_KEY] = request.get_full_path()
    return redirect('administracao:login')


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        login_redirect = _clean_login_redirect(request)
        if login_redirect is not None:
            return login_redirect
        if not (request.user.is_superuser or request.user.role in {request.user.Role.ADMIN_TOTAL, request.user.Role.ADMIN_JUNIOR}):
            return HttpResponseForbidden('Acesso administrativo necessário.')
        return view_func(request, *args, **kwargs)
    return wrapper


def superadmin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        login_redirect = _clean_login_redirect(request)
        if login_redirect is not None:
            return login_redirect
        if not request.user.is_superuser:
            return HttpResponseForbidden('Apenas o Superadministrador pode executar esta ação.')
        return view_func(request, *args, **kwargs)
    return wrapper


def table_manager_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        login_redirect = _clean_login_redirect(request)
        if login_redirect is not None:
            return login_redirect
        if not request.user.can_manage_tables:
            messages.error(request, 'Sua conta não possui permissão para excluir tabelas.')
            return redirect('administracao:alertas')
        return view_func(request, *args, **kwargs)
    return wrapper


def commercial_manager_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        login_redirect = _clean_login_redirect(request)
        if login_redirect is not None:
            return login_redirect
        if not (request.user.is_superuser or request.user.role == request.user.Role.ADMIN_TOTAL):
            return HttpResponseForbidden('Apenas o Superadministrador ou Administrador Total pode gerenciar clientes e planos.')
        return view_func(request, *args, **kwargs)
    return wrapper
