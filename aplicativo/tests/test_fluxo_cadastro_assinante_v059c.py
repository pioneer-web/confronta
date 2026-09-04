from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase

class FluxoCadastroAssinanteV059CTests(SimpleTestCase):
    def _read(self, rel):
        return (Path(settings.BASE_DIR) / rel).read_text(encoding='utf-8')

    def test_mensal_abre_novo_cadastro(self):
        html = self._read('aplicativo/templates/aplicativo/home.html')
        self.assertIn("?ciclo=MONTHLY&novo=1", html)

    def test_anual_abre_novo_cadastro(self):
        html = self._read('aplicativo/templates/aplicativo/home.html')
        self.assertIn("?ciclo=YEARLY&novo=1", html)

    def test_cadastro_trata_sessao_admin_do_cta_publico(self):
        source = self._read('aplicativo/views/auth.py')
        self.assertIn("novo_cadastro_publico", source)
        self.assertIn("request.GET.get('novo')", source)
        self.assertIn("logout(request)", source)

    def test_cliente_existente_nao_duplica_cadastro(self):
        source = self._read('aplicativo/views/auth.py')
        self.assertIn("return redirect('aplicativo:planos')", source)
