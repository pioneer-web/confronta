from django.test import TestCase
from django.urls import reverse
from administracao.datasets import get_dataset
from administracao.models import User

class ManageConfrontaSmokeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(email='admin@test.local', password='SenhaForte123!')

    def test_sicar_dataset_registry_loaded(self):
        self.assertIsNotNone(get_dataset('sicar-perimetros'))

    def test_login_page(self):
        response = self.client.get(reverse('administracao:login'))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('administracao:dashboard'))
        self.assertEqual(response.status_code, 302)
