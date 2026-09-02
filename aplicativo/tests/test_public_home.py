from django.test import TestCase, override_settings
from django.urls import reverse

from aplicativo.models import PlanoComercial


class PublicHomeTests(TestCase):
    def setUp(self):
        self.plano = PlanoComercial.objects.get(slug='confronta')
        self.plano.ativo = True
        self.plano.save(update_fields=['ativo'])

    def test_raiz_abre_home_publica_com_duas_modalidades(self):
        response = self.client.get(reverse('public_root'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Um CONFRONTA. Duas formas de assinar.')
        self.assertContains(response, 'R$ 67,90/mês')
        self.assertContains(response, 'R$ 49,90/mês')
        self.assertContains(response, 'R$ 598,80 cobrados anualmente')
        self.assertContains(response, 'O que é o CONFRONTA')

    def test_home_possui_link_para_login_do_aplicativo(self):
        response = self.client.get(reverse('public_root'))
        self.assertContains(response, reverse('aplicativo:login'))

    @override_settings(CONFRONTA_COMMERCIAL_CONTACT_URL='https://wa.me/5587999999999')
    def test_home_preserva_link_comercial_seguro(self):
        response = self.client.get(reverse('public_root'))
        self.assertContains(response, 'https://wa.me/5587999999999')

    @override_settings(CONFRONTA_COMMERCIAL_CONTACT_URL='javascript:alert(1)')
    def test_home_descarta_link_comercial_inseguro(self):
        response = self.client.get(reverse('public_root'))
        self.assertNotContains(response, 'javascript:alert(1)')
