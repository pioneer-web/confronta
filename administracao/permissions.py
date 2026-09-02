from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect


def admin_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_superuser or request.user.role in {request.user.Role.ADMIN_TOTAL, request.user.Role.ADMIN_JUNIOR}):
            return HttpResponseForbidden('Acesso administrativo necessário.')
        return view_func(request, *args, **kwargs)
    return wrapper


def superadmin_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            return HttpResponseForbidden('Apenas o Superadministrador pode executar esta ação.')
        return view_func(request, *args, **kwargs)
    return wrapper


def table_manager_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.can_manage_tables:
            messages.error(request, 'Sua conta não possui permissão para excluir tabelas.')
            return redirect('administracao:alertas')
        return view_func(request, *args, **kwargs)
    return wrapper


def commercial_manager_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_superuser or request.user.role == request.user.Role.ADMIN_TOTAL):
            return HttpResponseForbidden('Apenas o Superadministrador ou Administrador Total pode gerenciar clientes e planos.')
        return view_func(request, *args, **kwargs)
    return wrapper
