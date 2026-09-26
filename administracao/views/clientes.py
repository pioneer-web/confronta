from datetime import timedelta

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods
from allauth.socialaccount.models import SocialAccount

from administracao.forms import ClienteAdminForm, ClienteNovoAdminForm
from administracao.permissions import commercial_manager_required
from administracao.services.auditoria import registrar_auditoria
from aplicativo.models import PerfilCliente, PlanoComercial
from billing.models import AsaasCheckout, AssinaturaAsaas, PagamentoAsaas


@commercial_manager_required
def lista_clientes(request):
    hoje = timezone.localdate()
    proximos_7 = hoje + timedelta(days=7)
    clientes = PerfilCliente.objects.select_related('usuario', 'plano_comercial', 'plano_desejado_comercial')

    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    plano = request.GET.get('plano', '').strip()

    if q:
        clientes = clientes.filter(
            Q(usuario__first_name__icontains=q)
            | Q(usuario__last_name__icontains=q)
            | Q(usuario__email__icontains=q)
            | Q(telefone__icontains=q)
        )
    if status == 'ativos':
        clientes = clientes.filter(ativo=True).filter(Q(inicio_acesso__isnull=True) | Q(inicio_acesso__lte=hoje)).filter(Q(fim_acesso__isnull=True) | Q(fim_acesso__gte=hoje))
    elif status == 'inativos':
        clientes = clientes.filter(ativo=False)
    elif status == 'expirados':
        clientes = clientes.filter(ativo=True, fim_acesso__lt=hoje)
    elif status == 'agendados':
        clientes = clientes.filter(ativo=True, inicio_acesso__gt=hoje)
    elif status == 'sem_plano':
        clientes = clientes.filter(plano=PerfilCliente.Plano.SEM_PLANO)
    elif status == 'vencendo':
        clientes = clientes.filter(ativo=True, fim_acesso__range=(hoje, proximos_7))

    if plano:
        clientes = clientes.filter(plano_comercial_id=plano)

    base = PerfilCliente.objects.all()
    contexto = {
        'clientes': clientes.order_by('-atualizado_em', 'usuario__first_name'),
        'planos': PlanoComercial.objects.order_by('ordem', 'nome'),
        'filtros': {'q': q, 'status': status, 'plano': plano},
        'total_clientes': base.count(),
        'ativos': base.filter(ativo=True).filter(Q(inicio_acesso__isnull=True) | Q(inicio_acesso__lte=hoje)).filter(Q(fim_acesso__isnull=True) | Q(fim_acesso__gte=hoje)).count(),
        'expirados': base.filter(ativo=True, fim_acesso__lt=hoje).count(),
        'vencendo': base.filter(ativo=True, fim_acesso__range=(hoje, proximos_7)).count(),
    }
    return render(request, 'administracao/clientes/lista.html', contexto)


@sensitive_post_parameters('password1', 'password2')
@commercial_manager_required
def novo_cliente(request):
    form = ClienteNovoAdminForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                perfil = form.save()
                registrar_auditoria(
                    request.user,
                    'CLIENTE_CRIADO',
                    'PerfilCliente',
                    perfil.pk,
                    {
                        'email': perfil.usuario.email,
                        'plano': perfil.plano,
                        'plano_comercial_id': perfil.plano_comercial_id,
                        'ativo': perfil.ativo,
                    },
                )
        except IntegrityError:
            form.add_error('email', 'Já existe uma conta cadastrada com este e-mail.')
        else:
            messages.success(request, 'Cliente criado com sucesso.')
            return redirect('administracao:clientes')

    return render(request, 'administracao/clientes/novo.html', {'form': form})


@sensitive_post_parameters('nova_senha1', 'nova_senha2')
@commercial_manager_required
def editar_cliente(request, pk):
    perfil = get_object_or_404(
        PerfilCliente.objects.select_related('usuario', 'plano_comercial', 'plano_desejado_comercial'),
        pk=pk,
    )
    form = ClienteAdminForm(request.POST or None, instance=perfil)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                atualizado = form.save()
                registrar_auditoria(
                    request.user,
                    'CLIENTE_ATUALIZADO',
                    'PerfilCliente',
                    atualizado.pk,
                    {
                        'email': atualizado.usuario.email,
                        'plano': atualizado.plano,
                        'plano_comercial_id': atualizado.plano_comercial_id,
                        'ativo': atualizado.ativo,
                        'fim_acesso': atualizado.fim_acesso.isoformat() if atualizado.fim_acesso else None,
                        'senha_alterada': bool(form.senha_alterada),
                    },
                )
        except IntegrityError:
            form.add_error('email', 'Já existe uma conta cadastrada com este e-mail.')
        else:
            if form.senha_alterada:
                messages.success(request, 'Cliente atualizado e senha redefinida com sucesso.')
            else:
                messages.success(request, 'Cliente atualizado com sucesso.')
            return redirect('administracao:clientes')

    return render(request, 'administracao/clientes/form.html', {
        'form': form,
        'cliente': perfil,
        'conta_google': SocialAccount.objects.filter(user=perfil.usuario, provider='google').exists(),
    })


@commercial_manager_required
def alternar_cliente(request, pk):
    perfil = get_object_or_404(PerfilCliente.objects.select_related('usuario'), pk=pk)
    if request.method != 'POST':
        return redirect('administracao:clientes')
    perfil.ativo = not perfil.ativo
    perfil.save(update_fields=['ativo', 'atualizado_em'])
    registrar_auditoria(
        request.user,
        'CLIENTE_STATUS_ALTERADO',
        'PerfilCliente',
        perfil.pk,
        {'email': perfil.usuario.email, 'ativo': perfil.ativo},
    )
    messages.success(request, f'Cliente {"ativado" if perfil.ativo else "desativado"} com sucesso.')
    return redirect('administracao:clientes')


def _cliente_tem_historico_financeiro(perfil):
    checkouts = AsaasCheckout.objects.filter(Q(perfil=perfil) | Q(usuario=perfil.usuario))
    assinaturas = AssinaturaAsaas.objects.filter(perfil=perfil)

    subscription_ids = list(
        assinaturas.exclude(asaas_subscription_id__isnull=True)
        .exclude(asaas_subscription_id='')
        .values_list('asaas_subscription_id', flat=True)
    )
    customer_ids = set(
        checkouts.exclude(asaas_customer_id='').values_list('asaas_customer_id', flat=True)
    )
    customer_ids.update(
        AssinaturaAsaas.objects.filter(perfil=perfil)
        .exclude(asaas_customer_id='')
        .values_list('asaas_customer_id', flat=True)
    )

    pagamentos = PagamentoAsaas.objects.filter(
        Q(assinatura__perfil=perfil)
        | Q(asaas_subscription_id__in=subscription_ids)
        | Q(asaas_customer_id__in=customer_ids)
    )
    return checkouts.exists() or assinaturas.exists() or pagamentos.exists()


@commercial_manager_required
@require_http_methods(['GET', 'POST'])
def excluir_cliente(request, pk):
    perfil = get_object_or_404(
        PerfilCliente.objects.select_related('usuario', 'plano_comercial'),
        pk=pk,
    )
    usuario = perfil.usuario
    if usuario.is_superuser or usuario.is_staff or usuario.role is not None:
        raise Http404

    historico_financeiro = _cliente_tem_historico_financeiro(perfil)
    if request.method == 'GET':
        return render(request, 'administracao/clientes/confirmar_exclusao.html', {
            'cliente': perfil,
            'historico_financeiro': historico_financeiro,
        })

    if historico_financeiro:
        if request.POST.get('desativar_cliente') == 'sim':
            perfil.ativo = False
            perfil.save(update_fields=['ativo', 'atualizado_em'])
            registrar_auditoria(
                request.user,
                'CLIENTE_STATUS_ALTERADO',
                'PerfilCliente',
                perfil.pk,
                {'email': usuario.email, 'ativo': False, 'motivo': 'historico_financeiro'},
            )
            messages.success(request, 'Cliente desativado com sucesso.')
            return redirect('administracao:clientes')
        return render(request, 'administracao/clientes/confirmar_exclusao.html', {
            'cliente': perfil,
            'historico_financeiro': True,
        })

    if request.POST.get('confirmar_exclusao') != 'sim':
        return render(request, 'administracao/clientes/confirmar_exclusao.html', {
            'cliente': perfil,
            'historico_financeiro': False,
            'erro_confirmacao': True,
        })

    perfil_id = perfil.pk
    user_id = usuario.pk
    nome = usuario.get_full_name().strip() or usuario.email
    email = usuario.email
    plano = perfil.nome_plano_atual
    executor = request.user
    with transaction.atomic():
        registrar_auditoria(
            executor,
            'CLIENTE_EXCLUIDO',
            'PerfilCliente',
            perfil_id,
            {
                'perfil_id': perfil_id,
                'user_id': user_id,
                'nome': nome,
                'email': email,
                'plano': plano,
                'executado_por_id': executor.pk,
                'executado_por_email': executor.email,
            },
        )
        usuario.delete()

    messages.success(request, 'Cliente excluído com sucesso.')
    return redirect('administracao:clientes')
