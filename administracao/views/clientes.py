from datetime import timedelta

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters

from administracao.forms import ClienteAdminForm, ClienteNovoAdminForm
from administracao.permissions import commercial_manager_required
from administracao.services.auditoria import registrar_auditoria
from aplicativo.models import PerfilCliente, PlanoComercial


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
            messages.success(request, 'Cliente atualizado com sucesso.')
            return redirect('administracao:clientes')

    return render(request, 'administracao/clientes/form.html', {
        'form': form,
        'cliente': perfil,
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
