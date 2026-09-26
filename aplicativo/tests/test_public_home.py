from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from administracao.models import User
from aplicativo.models import PerfilCliente, PlanoComercial


class PublicHomeTests(TestCase):
    def setUp(self):
        self.plano = PlanoComercial.objects.get(slug='confronta')
        self.plano.ativo = True
        self.plano.preco_mensal = Decimal('89.90')
        self.plano.preco_anual = Decimal('898.80')
        self.plano.save(
            update_fields=[
                'ativo',
                'preco_mensal',
                'preco_anual',
            ]
        )

    def test_raiz_abre_home_publica_com_novo_funil_e_planos_dinamicos(self):
        response = self.client.get(reverse('public_root'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Criar conta grátis')
        self.assertContains(response, 'Consulte CAR e informações territoriais de imóveis rurais em um único mapa')
        self.assertContains(response, 'Como funciona')
        self.assertContains(response, 'Conteúdos')
        self.assertContains(response, 'Perguntas sobre o CONFRONTA')
        self.assertContains(response, 'R$ 89,90/mês')
        self.assertContains(response, 'R$ 74,90/mês')
        self.assertContains(response, 'R$ 898,80')
        self.assertContains(response, 'src="/static/aplicativo/img/hero.png"')
        self.assertContains(response, f'href="{reverse("aplicativo:cadastro")}"')
        self.assertNotContains(response, f'cadastro_mensal')
        self.assertNotContains(response, f'cadastro_anual')
        self.assertIn(b'"@type": "FAQPage"', response.content)

    def test_home_possui_link_para_login_do_aplicativo(self):
        response = self.client.get(reverse('public_root'))
        self.assertContains(response, reverse('aplicativo:login'))
        self.assertContains(response, 'class="cfp-nav-login"')
        self.assertNotContains(
            response,
            f'class="cfp-nav-cta" href="{reverse("aplicativo:login")}"',
        )

    def test_usuario_autenticado_recebe_link_para_mapa_sem_cta_de_cadastro(self):
        user = User.objects.create_user(email='home-cliente@test.local', password='SenhaForte123!')
        PerfilCliente.objects.create(usuario=user, plano=PerfilCliente.Plano.SEM_PLANO)
        self.client.force_login(user)

        response = self.client.get(reverse('public_root'))

        self.assertContains(response, 'Ir para o mapa')
        self.assertNotContains(response, 'Criar conta grátis')

    def test_robots_sitemap_e_llms_continuam_publicos(self):
        for name in ('robots_txt', 'sitemap_xml', 'llms_txt'):
            with self.subTest(endpoint=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)

    @override_settings(CONFRONTA_COMMERCIAL_CONTACT_URL='https://wa.me/5587999999999')
    def test_home_preserva_link_comercial_seguro(self):
        response = self.client.get(reverse('public_root'))
        self.assertContains(response, 'https://wa.me/5587999999999')

    @override_settings(CONFRONTA_COMMERCIAL_CONTACT_URL='javascript:alert(1)')
    def test_home_descarta_link_comercial_inseguro(self):
        response = self.client.get(reverse('public_root'))
        self.assertNotContains(response, 'javascript:alert(1)')
