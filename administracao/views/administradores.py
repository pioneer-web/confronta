from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from administracao.forms import AdministradorCreateForm, AdministradorUpdateForm
from administracao.models import User
from administracao.permissions import superadmin_required
from administracao.services.auditoria import registrar_auditoria


@superadmin_required
def lista_administradores(request):
    usuarios = User.objects.filter(is_superuser=False, role__in=[User.Role.ADMIN_TOTAL, User.Role.ADMIN_JUNIOR]).order_by('first_name', 'email')
    return render(request, 'administracao/administradores/lista.html', {'usuarios': usuarios})


@superadmin_required
def criar_administrador(request):
    form = AdministradorCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        registrar_auditoria(request.user, 'ADMIN_CRIADO', 'User', user.pk, {'email': user.email, 'role': user.role})
        messages.success(request, 'Administrador criado com sucesso.')
        return redirect('administracao:administradores')
    return render(request, 'administracao/administradores/form.html', {'form': form, 'titulo': 'Novo administrador'})


@superadmin_required
def editar_administrador(request, pk):
    user = get_object_or_404(User, pk=pk, is_superuser=False, role__in=[User.Role.ADMIN_TOTAL, User.Role.ADMIN_JUNIOR])
    form = AdministradorUpdateForm(request.POST or None, instance=user)
    if request.method == 'POST' and form.is_valid():
        updated = form.save()
        registrar_auditoria(request.user, 'ADMIN_ATUALIZADO', 'User', updated.pk, {'email': updated.email, 'role': updated.role, 'is_active': updated.is_active})
        messages.success(request, 'Administrador atualizado com sucesso.')
        return redirect('administracao:administradores')
    return render(request, 'administracao/administradores/form.html', {'form': form, 'titulo': 'Editar administrador'})
