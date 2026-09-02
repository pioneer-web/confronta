from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from administracao.forms import LoginForm


@sensitive_post_parameters('password')
@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect('administracao:dashboard')
    form = LoginForm(request.POST or None, request=request)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        proximo = request.GET.get('next')
        if proximo and url_has_allowed_host_and_scheme(
            proximo, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(proximo)
        return redirect('administracao:dashboard')
    return render(request, 'administracao/login.html', {'form': form})


def logout_view(request):
    if request.method == 'POST':
        logout(request)
    return redirect('administracao:login')
