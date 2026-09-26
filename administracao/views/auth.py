from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from administracao.forms import LoginForm
from administracao.permissions import ADMIN_LOGIN_NEXT_SESSION_KEY
from aplicativo.security import (
    limpar_falhas_login,
    registrar_falha_login,
    verificar_login,
)


def _pop_safe_admin_next(request):
    proximo = str(request.session.pop(ADMIN_LOGIN_NEXT_SESSION_KEY, '') or '')
    if not proximo.startswith('/painel/'):
        return ''
    if not url_has_allowed_host_and_scheme(
        proximo,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return ''
    return proximo


@sensitive_post_parameters('password')
@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect('administracao:dashboard')

    # Canonicaliza links antigos como /painel/login/?next=/painel/.
    # O destino protegido fica na sessão, não na URL.
    if request.method == 'GET' and request.META.get('QUERY_STRING'):
        return redirect('administracao:login')

    form = LoginForm(request.POST or None, request=request)
    status = 200
    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip().lower()
        estado = verificar_login(request, email, administrativo=True)
        if not estado.permitido:
            form.add_error(
                None,
                'Muitas tentativas de acesso. Aguarde alguns minutos e tente novamente.',
            )
            status = 429
        elif form.is_valid():
            limpar_falhas_login(request, email, administrativo=True)
            login(request, form.get_user())
            proximo = _pop_safe_admin_next(request)
            if proximo:
                return redirect(proximo)
            return redirect('administracao:dashboard')
        else:
            registrar_falha_login(request, email, administrativo=True)

    return render(request, 'administracao/login.html', {'form': form}, status=status)


def logout_view(request):
    if request.method == 'POST':
        logout(request)
    return redirect('administracao:login')
