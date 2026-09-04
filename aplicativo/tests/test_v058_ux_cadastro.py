from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase


class V058UxCadastroTests(SimpleTestCase):
    def _read(self, relative):
        return (Path(settings.BASE_DIR) / relative).read_text(encoding='utf-8')

    def test_senha_minima_e_oito(self):
        minimum = [
            item for item in settings.AUTH_PASSWORD_VALIDATORS
            if item['NAME'].endswith('MinimumLengthValidator')
        ][0]
        self.assertEqual(minimum['OPTIONS']['min_length'], 8)

    def test_cadastro_tem_olho_nos_dois_campos(self):
        html = self._read('aplicativo/templates/aplicativo/cadastro.html')
        self.assertIn('data-password-toggle="id_password1"', html)
        self.assertIn('data-password-toggle="id_password2"', html)

    def test_comunicados_estao_no_menu(self):
        html = self._read('aplicativo/templates/aplicativo/dashboard.html')
        self.assertIn('id="rail-comunicados"', html)

    def test_chat_flutuante_existe(self):
        html = self._read('aplicativo/templates/aplicativo/dashboard.html')
        self.assertIn('id="client-chat-fab"', html)

    def test_medidor_tem_acoes_acessiveis(self):
        js = self._read('aplicativo/static/aplicativo/js/medir-distancia.js')
        self.assertIn('distance-measure-manager', js)
        self.assertIn("element.setAttribute('tabindex', '0')", js)
        self.assertIn("selectedLayer.editing.enable()", js)
