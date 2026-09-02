from django.test import TestCase

from administracao.models import User
from aplicativo.access import resolver_acesso_aplicativo
from aplicativo.models import PerfilCliente


class AcessoAplicativoTests(TestCase):
    def test_superadministrador_recebe_total(self):
        user = User.objects.create_superuser(email='super@test.local', password='SenhaForte123!')
        acesso = resolver_acesso_aplicativo(user)
        self.assertEqual(acesso.plano, PerfilCliente.Plano.TOTAL)
        self.assertTrue(acesso.pode_desenhar_glebas)

    def test_admin_total_recebe_total(self):
        user = User.objects.create_user(
            email='admin.total@test.local',
            password='SenhaForte123!',
            role=User.Role.ADMIN_TOTAL,
            is_staff=True,
        )
        acesso = resolver_acesso_aplicativo(user)
        self.assertEqual(acesso.plano, PerfilCliente.Plano.TOTAL)

    def test_admin_junior_recebe_basico(self):
        user = User.objects.create_user(
            email='admin.junior@test.local',
            password='SenhaForte123!',
            role=User.Role.ADMIN_JUNIOR,
            is_staff=True,
        )
        acesso = resolver_acesso_aplicativo(user)
        self.assertEqual(acesso.plano, PerfilCliente.Plano.BASICO)
        self.assertFalse(acesso.pode_desenhar_glebas)
