from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from administracao.models import User


class RouteTests(TestCase):
    def test_root_nao_e_login(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Inteligência Territorial Rural')

    def test_painel_redireciona_para_login_sem_next_na_url(self):
        r = self.client.get('/painel/')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, '/painel/login/')
        self.assertNotIn('?', r.url)

    def test_login_legado_com_next_canonicaliza_para_rota_limpa(self):
        r = self.client.get('/painel/login/?next=/painel/')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, '/painel/login/')

    def test_admin_logado_abre_painel(self):
        u = User.objects.create_user(
            email='jr@test.local',
            password='SenhaForte123!',
            role=User.Role.ADMIN_JUNIOR,
        )
        self.client.force_login(u)
        self.assertEqual(self.client.get('/painel/').status_code, 200)

    def test_rotas_limpas_de_importacao_por_fonte(self):
        self.assertEqual(
            reverse('administracao:novo_lote_importacao_fonte', kwargs={'fonte_slug': 'sicar'}),
            '/painel/importacoes/lote/novo/sicar/',
        )
        self.assertEqual(
            reverse(
                'administracao:novo_lote_importacao_fonte_uf',
                kwargs={'fonte_slug': 'sicar', 'uf': 'PE'},
            ),
            '/painel/importacoes/lote/novo/sicar/PE/',
        )

    def test_url_legada_de_fonte_redireciona_para_rota_limpa(self):
        u = User.objects.create_user(
            email='admin@test.local',
            password='SenhaForte123!',
            role=User.Role.ADMIN_TOTAL,
        )
        self.client.force_login(u)
        r = self.client.get('/painel/importacoes/lote/novo/?fonte=sicar&uf=PE')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, '/painel/importacoes/lote/novo/sicar/PE/')

    def test_templates_de_navegacao_nao_usam_query_fonte(self):
        rel_paths = [
            'administracao/templates/administracao/base.html',
            'administracao/templates/administracao/dashboard.html',
            'administracao/templates/administracao/bases/detalhe.html',
            'administracao/templates/administracao/importacoes/fonte.html',
            'administracao/templates/administracao/importacoes/sicar.html',
        ]
        for rel_path in rel_paths:
            content = (Path(settings.BASE_DIR) / rel_path).read_text(encoding='utf-8')
            self.assertNotIn('?fonte=', content, rel_path)
