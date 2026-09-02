from pathlib import Path

from django.test import SimpleTestCase


class SicarManualV03Tests(SimpleTestCase):
    def test_compose_does_not_start_sicar_monitor(self):
        compose = (Path(__file__).resolve().parents[2] / 'docker-compose.yml').read_text(encoding='utf-8')
        self.assertNotIn('sicar_monitor:', compose)
        self.assertNotIn('source_monitor:', compose)

    def test_sicar_page_has_state_selector_and_no_auto_button(self):
        template = (Path(__file__).resolve().parents[1] / 'templates/administracao/importacoes/sicar.html').read_text(encoding='utf-8')
        self.assertIn('name="uf"', template)
        self.assertNotIn('Verificar SICAR PE agora', template)
        self.assertIn('Importar arquivos de', template)
