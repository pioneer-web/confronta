from django.test import TestCase
from django.urls import reverse

from administracao.models import User
from administracao.source_catalog import SOURCE_BY_SLUG


class UnifiedProjectTests(TestCase):
    def setUp(self):
        self.super = User.objects.create_superuser(email='root@confronta.local', password='SenhaForte123!')
        self.client.force_login(self.super)

    def test_saas_e_manage_compartilham_o_mesmo_projeto(self):
        self.assertEqual(reverse('administracao:dashboard'), '/painel/')
        self.assertTrue(reverse('aplicativo:inicio').startswith('/mapa/'))

    def test_fontes_prioritarias_estao_no_manage(self):
        for slug in ('sicar', 'prodes', 'ibama', 'incra', 'sigef', 'sncr', 'sicor', 'funai'):
            self.assertIn(slug, SOURCE_BY_SLUG)

    def test_fontes_retiradas_nao_estao_no_catalogo(self):
        for slug in ('florestas-publicas', 'deter', 'ana', 'anm', 'zarc', 'mapbiomas'):
            self.assertNotIn(slug, SOURCE_BY_SLUG)
