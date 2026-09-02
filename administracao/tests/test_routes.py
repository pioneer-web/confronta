from django.test import TestCase
from django.urls import reverse
from administracao.models import User
class RouteTests(TestCase):
    def test_root_nao_e_login(self):
        r=self.client.get('/'); self.assertEqual(r.status_code,200); self.assertContains(r,'Área pública institucional')
    def test_painel_redireciona_para_login(self):
        r=self.client.get('/painel/'); self.assertEqual(r.status_code,302); self.assertIn('/painel/login/',r.url)
    def test_admin_logado_abre_painel(self):
        u=User.objects.create_user(email='jr@test.local',password='SenhaForte123!',role=User.Role.ADMIN_JUNIOR); self.client.force_login(u); self.assertEqual(self.client.get('/painel/').status_code,200)
