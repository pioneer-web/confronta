from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

class RotasAssinaturaV060Tests(SimpleTestCase):
    def _read(self, rel):
        return (Path(settings.BASE_DIR) / rel).read_text(encoding='utf-8')

    def test_rotas_sem_query_string(self):
        self.assertEqual(reverse('aplicativo:cadastro_mensal'), '/mapa/cadastro/mensal/')
        self.assertEqual(reverse('aplicativo:cadastro_anual'), '/mapa/cadastro/anual/')

    def test_home_usa_rotas_nomeadas(self):
        html = self._read('aplicativo/templates/aplicativo/home.html')
        self.assertIn("{% url 'aplicativo:cadastro_mensal' %}", html)
        self.assertIn("{% url 'aplicativo:cadastro_anual' %}", html)

    def test_cadastro_sem_parametros_de_ciclo(self):
        html = self._read('aplicativo/templates/aplicativo/cadastro.html')
        self.assertNotIn('name="ciclo"', html)
        self.assertNotIn('?ciclo=', html)
        self.assertNotIn('?novo=', html)

    def test_cadastro_tem_login_no_rodape(self):
        html = self._read('aplicativo/templates/aplicativo/cadastro.html')
        self.assertIn('Já é cadastrado?', html)
        self.assertIn("{% url 'aplicativo:login' %}", html)

    def test_backend_define_ciclo_pela_rota(self):
        source = self._read('aplicativo/views/auth.py')
        self.assertIn("'mensal': AsaasCheckout.Ciclo.MONTHLY", source)
        self.assertIn("'anual': AsaasCheckout.Ciclo.YEARLY", source)
        self.assertNotIn("request.GET.get('ciclo')", source)
        self.assertNotIn("request.POST.get('ciclo')", source)
