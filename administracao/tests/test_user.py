from django.test import TestCase
from administracao.models import User


class UserTests(TestCase):
    def test_superadmin_nao_recebe_role_operacional(self):
        user = User.objects.create_superuser(email='SUPER@TEST.LOCAL', password='SenhaForte123!', role=User.Role.ADMIN_TOTAL)
        self.assertTrue(user.is_superuser)
        self.assertIsNone(user.role)
        self.assertEqual(user.email, 'super@test.local')

    def test_total_gerencia_tabelas_e_junior_nao(self):
        total = User.objects.create_user(email='total@test.local', password='SenhaForte123!', role=User.Role.ADMIN_TOTAL)
        junior = User.objects.create_user(email='junior@test.local', password='SenhaForte123!', role=User.Role.ADMIN_JUNIOR)
        self.assertTrue(total.can_manage_tables)
        self.assertFalse(junior.can_manage_tables)
