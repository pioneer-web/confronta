from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase


class FluxoAssinaturaV059BTests(SimpleTestCase):
    def _read(self, rel):
        return (Path(settings.BASE_DIR) / rel).read_text(encoding='utf-8')

    def test_login_conhecer_planos_vai_para_home_planos(self):
        html = self._read('aplicativo/templates/aplicativo/login.html')
        self.assertIn("{% url 'public_root' %}#planos", html)

    def test_cta_comecar_agora_vai_para_planos(self):
        html = self._read('aplicativo/templates/aplicativo/home.html')
        self.assertIn('href="#planos">Começar agora</a>', html)

    def test_plano_mensal_vai_para_cadastro_com_ciclo(self):
        html = self._read('aplicativo/templates/aplicativo/home.html')
        self.assertIn("{% url 'aplicativo:cadastro' %}?ciclo=MONTHLY", html)

    def test_plano_anual_vai_para_cadastro_com_ciclo(self):
        html = self._read('aplicativo/templates/aplicativo/home.html')
        self.assertIn("{% url 'aplicativo:cadastro' %}?ciclo=YEARLY", html)
