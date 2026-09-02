from django.test import TestCase
from django.urls import reverse

from administracao.models import User


class PermissionTests(TestCase):
    def setUp(self):
        self.super = User.objects.create_superuser(email='super@test.local', password='SenhaForte123!')
        self.total = User.objects.create_user(email='total@test.local', password='SenhaForte123!', role=User.Role.ADMIN_TOTAL)
        self.junior = User.objects.create_user(email='junior@test.local', password='SenhaForte123!', role=User.Role.ADMIN_JUNIOR)

    def test_admin_total_acessa_operacao_e_gestao_comercial(self):
        self.client.force_login(self.total)
        self.assertEqual(self.client.get(reverse('administracao:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('administracao:clientes')).status_code, 200)
        self.assertEqual(self.client.get(reverse('administracao:planos')).status_code, 200)
        self.assertEqual(self.client.get(reverse('administracao:lotes_recentes')).status_code, 200)

    def test_junior_opera_dados_mas_nao_gerencia_clientes(self):
        self.client.force_login(self.junior)
        self.assertEqual(self.client.get(reverse('administracao:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('administracao:lotes_recentes')).status_code, 200)
        self.assertEqual(self.client.get(reverse('administracao:clientes')).status_code, 403)
        self.assertEqual(self.client.get(reverse('administracao:planos')).status_code, 403)

    def test_apenas_superadmin_gerencia_administradores(self):
        self.client.force_login(self.total)
        self.assertEqual(self.client.get(reverse('administracao:administradores')).status_code, 403)
        self.client.force_login(self.super)
        self.assertEqual(self.client.get(reverse('administracao:administradores')).status_code, 200)
