from django.test import TestCase

from administracao.models import User
from aplicativo.forms import ClienteLoginForm
from aplicativo.models import PerfilCliente


class ClienteLoginFormTests(TestCase):
    def test_cliente_ativo_autentica(self):
        user = User.objects.create_user(email='cliente@test.local', password='SenhaForte123!')
        PerfilCliente.objects.create(usuario=user, plano=PerfilCliente.Plano.BASICO)
        form = ClienteLoginForm(data={'email': 'cliente@test.local', 'password': 'SenhaForte123!'})
        self.assertTrue(form.is_valid())

    def test_cliente_sem_plano_autentica(self):
        user = User.objects.create_user(email='semplano@test.local', password='SenhaForte123!')
        PerfilCliente.objects.create(usuario=user, plano=PerfilCliente.Plano.SEM_PLANO)
        form = ClienteLoginForm(data={'email': 'semplano@test.local', 'password': 'SenhaForte123!'})
        self.assertTrue(form.is_valid())

    def test_admin_total_autentica_no_aplicativo_sem_perfil_cliente(self):
        User.objects.create_user(
            email='admin.total@test.local', password='SenhaForte123!',
            role=User.Role.ADMIN_TOTAL, is_staff=True,
        )
        form = ClienteLoginForm(data={'email': 'admin.total@test.local', 'password': 'SenhaForte123!'})
        self.assertTrue(form.is_valid())

    def test_admin_junior_autentica_no_aplicativo_sem_perfil_cliente(self):
        User.objects.create_user(
            email='admin.junior@test.local', password='SenhaForte123!',
            role=User.Role.ADMIN_JUNIOR, is_staff=True,
        )
        form = ClienteLoginForm(data={'email': 'admin.junior@test.local', 'password': 'SenhaForte123!'})
        self.assertTrue(form.is_valid())

    def test_superadministrador_autentica_no_aplicativo_sem_perfil_cliente(self):
        User.objects.create_superuser(email='super@test.local', password='SenhaForte123!')
        form = ClienteLoginForm(data={'email': 'super@test.local', 'password': 'SenhaForte123!'})
        self.assertTrue(form.is_valid())

    def test_usuario_comum_sem_perfil_cliente_e_bloqueado(self):
        User.objects.create_user(email='sem.perfil@test.local', password='SenhaForte123!')
        form = ClienteLoginForm(data={'email': 'sem.perfil@test.local', 'password': 'SenhaForte123!'})
        self.assertFalse(form.is_valid())
        self.assertIn('não possui acesso à Área Aplicativo', str(form.errors))
