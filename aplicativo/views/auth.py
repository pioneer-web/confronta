from django.contrib import messages
from django.contrib.auth import login, logout
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from urllib.parse import urlsplit

from administracao.models import User
from aplicativo.access import resolver_acesso_aplicativo
from aplicativo.forms import CadastroClienteForm, ClienteLoginForm
from aplicativo.models import PerfilCliente, PlanoComercial
from aplicativo.permissions import SESSION_LOGOUT_LOCAL
from aplicativo.security import (
    limpar_falhas_login,
    minutos_para_mensagem,
    registrar_falha_login,
    registrar_sinal_bot,
    verificar_login,
)
from aplicativo.session_keys import SESSION_CAR_ATUAL
from billing.models import AsaasCheckout
from billing.services.asaas import AsaasAPIError, AsaasConfigurationError
from billing.services.checkout import criar_checkout


HONEYPOT_FIELD = '_contact_website'


def _url_comercial_segura(valor: str) -> str:
    valor = (valor or '').strip()
    if not valor:
        return ''
    parsed = urlsplit(valor)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return ''
    return valor


@sensitive_post_parameters('password')
@never_cache
def login_view(request):
    acesso_atual = resolver_acesso_aplicativo(request.user) if request.user.is_authenticated else None
    logout_local = bool(request.session.get(SESSION_LOGOUT_LOCAL))
    if acesso_atual is not None and not logout_local:
        return redirect('aplicativo:inicio')

    form = ClienteLoginForm(request.POST or None, request=request)
    status = 200

    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip().lower()

        if request.POST.get(HONEYPOT_FIELD):
            registrar_sinal_bot(request, origem='login_cliente')
            form.add_error(None, 'Não foi possível processar o acesso. Tente novamente mais tarde.')
            status = 429
        else:
            estado = verificar_login(request, email, administrativo=False)
            if not estado.permitido:
                form.add_error(
                    None,
                    f'Muitas tentativas de acesso. Tente novamente em aproximadamente {minutos_para_mensagem(estado.repetir_em_segundos)} minuto(s).',
                )
                status = 429
            elif form.is_valid():
                limpar_falhas_login(request, email, administrativo=False)
                login(request, form.get_user())
                request.session.pop(SESSION_LOGOUT_LOCAL, None)
                return redirect('aplicativo:inicio')
            else:
                estado = registrar_falha_login(request, email, administrativo=False)
                if not estado.permitido:
                    form.add_error(
                        None,
                        f'Limite de tentativas atingido. Tente novamente em aproximadamente {minutos_para_mensagem(estado.repetir_em_segundos)} minuto(s).',
                    )
                    status = 429

    return render(request, 'aplicativo/login.html', {
        'form': form,
        'honeypot_field': HONEYPOT_FIELD,
    }, status=status)


@sensitive_post_parameters('password1', 'password2')
@never_cache
def cadastro_view(request):
    """Cadastro do cliente + início do Checkout Asaas.

    A conta nasce sem plano pago. Somente o webhook CHECKOUT_PAID promove o
    PerfilCliente para o plano comercial CONFRONTA e define a vigência.
    """
    if request.user.is_authenticated:
        acesso_atual = resolver_acesso_aplicativo(request.user)
        if acesso_atual is not None:
            if acesso_atual.eh_administrador:
                return redirect(
                    'aplicativo:login'
                    if request.session.get(SESSION_LOGOUT_LOCAL)
                    else 'aplicativo:inicio'
                )
            return redirect('aplicativo:planos')

    plano = PlanoComercial.objects.filter(slug='confronta', ativo=True).first()
    ciclo = (request.POST.get('ciclo') or request.GET.get('ciclo') or 'MONTHLY').strip().upper()
    if ciclo not in {AsaasCheckout.Ciclo.MONTHLY, AsaasCheckout.Ciclo.YEARLY}:
        ciclo = AsaasCheckout.Ciclo.MONTHLY

    form = CadastroClienteForm(request.POST or None)
    status = 200

    if request.method == 'POST':
        if request.POST.get(HONEYPOT_FIELD):
            registrar_sinal_bot(request, origem='cadastro_cliente')
            form.add_error(None, 'Não foi possível processar o cadastro. Tente novamente mais tarde.')
            status = 429
        elif plano is None:
            form.add_error(None, 'O plano de contratação ainda não está disponível.')
        elif form.is_valid():
            nome = form.cleaned_data['nome']
            partes = nome.split(' ', 1)
            first_name = partes[0]
            last_name = partes[1] if len(partes) > 1 else ''
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        email=form.cleaned_data['email'],
                        password=form.cleaned_data['password1'],
                        first_name=first_name,
                        last_name=last_name,
                        is_active=True,
                        is_staff=False,
                        role=None,
                    )
                    perfil = PerfilCliente.objects.create(
                        usuario=user,
                        telefone=form.cleaned_data['telefone'],
                        plano=PerfilCliente.Plano.SEM_PLANO,
                        plano_desejado=plano.nivel_acesso,
                        plano_desejado_comercial=plano,
                        ativo=True,
                        renovacao_automatica=False,
                    )
            except IntegrityError:
                form.add_error('email', 'Já existe uma conta cadastrada com este e-mail.')
            else:
                login(request, user)
                request.session.pop(SESSION_LOGOUT_LOCAL, None)
                try:
                    checkout = criar_checkout(request, perfil, ciclo)
                except (AsaasConfigurationError, AsaasAPIError, RuntimeError, ValueError) as exc:
                    messages.error(
                        request,
                        'Sua conta foi criada normalmente, mas não foi possível iniciar o pagamento agora. Tente novamente em alguns instantes.',
                    )
                    return redirect('aplicativo:planos')
                return redirect(checkout.checkout_url)

    return render(request, 'aplicativo/cadastro.html', {
        'form': form,
        'plano_selecionado': plano,
        'ciclo': ciclo,
        'honeypot_field': HONEYPOT_FIELD,
    }, status=status)


def logout_view(request):
    if request.method == 'POST':
        request.session.pop(SESSION_CAR_ATUAL, None)
        acesso = resolver_acesso_aplicativo(request.user) if request.user.is_authenticated else None
        if acesso is not None and acesso.eh_administrador:
            request.session[SESSION_LOGOUT_LOCAL] = True
        else:
            logout(request)
    return redirect(reverse('aplicativo:login'))
