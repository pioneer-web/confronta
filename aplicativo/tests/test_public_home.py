from decimal import Decimal
import json
import re

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
        for uf in ('AL', 'BA', 'CE', 'MA', 'PB', 'PE', 'PI', 'RN', 'SE'):
            self.assertContains(response, uf)
        self.assertContains(response, 'Cobertura atual')
        self.assertContains(response, 'Região Nordeste')

    def test_faq_de_cobertura_visivel_e_json_ld_coerente(self):
        response = self.client.get(reverse('public_root'))
        pergunta = 'O CONFRONTA atende todo o Brasil?'
        resposta = (
            'Ainda não. Neste momento, o CONFRONTA atende imóveis rurais localizados em '
            'AL, BA, CE, MA, PB, PE, PI, RN, SE. A cobertura será ampliada gradualmente.'
        )
        self.assertContains(response, pergunta)
        self.assertContains(response, resposta)
        scripts = re.findall(
            rb'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>',
            response.content,
            re.DOTALL,
        )
        faq = next(json.loads(script) for script in scripts if b'FAQPage' in script)
        faq_item = next(item for item in faq['mainEntity'] if item['name'] == pergunta)
        self.assertEqual(faq_item['acceptedAnswer']['text'], resposta)

    def test_pagina_planos_exibe_cobertura_antes_dos_planos(self):
        user = User.objects.create_user(email='planos-cliente@test.local', password='SenhaForte123!')
        PerfilCliente.objects.create(usuario=user, plano=PerfilCliente.Plano.SEM_PLANO)
        self.client.force_login(user)
        response = self.client.get(reverse('aplicativo:planos'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cobertura atual do CONFRONTA')
        self.assertContains(response, 'Alagoas, Bahia, Ceará, Maranhão, Paraíba, Pernambuco, Piauí, Rio Grande do Norte e Sergipe')
        self.assertContains(response, 'name="ciclo" value="MONTHLY"')
        self.assertContains(response, '89,90')
        self.assertContains(response, 'R$ 898,80')

    def test_home_continua_usando_precos_do_plano_comercial(self):
        self.plano.preco_mensal = Decimal('123.45')
        self.plano.preco_anual = Decimal('1234.50')
        self.plano.save(update_fields=['preco_mensal', 'preco_anual'])
        response = self.client.get(reverse('public_root'))
        self.assertContains(response, 'R$ 123,45/mês')
        self.assertContains(response, 'R$ 1234,50')

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
