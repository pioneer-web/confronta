from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class PolygonosUiV056Tests(SimpleTestCase):
    def _read(self, relative):
        return (Path(settings.BASE_DIR) / relative).read_text(encoding="utf-8")

    def test_menu_lateral_usa_poligono_e_novo_icone(self):
        template = self._read("aplicativo/templates/aplicativo/dashboard.html")
        self.assertIn('client-rail-label">Polígono</span>', template)
        self.assertIn('class="polygon-tool-icon"', template)

    def test_workflow_tem_importacao_e_exportacoes(self):
        template = self._read("aplicativo/templates/aplicativo/dashboard.html")
        self.assertIn('id="gleba-import-dropzone"', template)
        self.assertIn('id="download-drawn-kml"', template)
        self.assertIn('id="download-drawn-csv"', template)

    def test_js_tem_preview_olho_e_csv(self):
        js = self._read("aplicativo/static/aplicativo/js/glebas.js")
        self.assertIn("function polygonPreviewSvg", js)
        self.assertIn("function toggleLayerVisibility", js)
        self.assertIn("function downloadLayerCsv", js)
        self.assertIn("function downloadAllCsv", js)
