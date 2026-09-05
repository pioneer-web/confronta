from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from administracao.forms import LoginForm
from administracao.permissions import ADMIN_LOGIN_NEXT_SESSION_KEY


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
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        proximo = _pop_safe_admin_next(request)
        if proximo:
            return redirect(proximo)
        return redirect('administracao:dashboard')
    return render(request, 'administracao/login.html', {'form': form})


def logout_view(request):
    if request.method == 'POST':
        logout(request)
    return redirect('administracao:login')
