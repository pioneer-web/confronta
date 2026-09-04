from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase
from aplicativo.models import AtendimentoCliente, AvisoCliente, MensagemAtendimento


class ComunicacaoSuporteV057Tests(SimpleTestCase):
    def _read(self, relative):
        return (Path(settings.BASE_DIR) / relative).read_text(encoding='utf-8')

    def test_aviso_tem_destinatario_opcional(self):
        field = AvisoCliente._meta.get_field('destinatario')
        self.assertTrue(field.null)
        self.assertTrue(field.blank)

    def test_modelos_de_chat_existem(self):
        self.assertTrue(AtendimentoCliente._meta.get_field('cliente').one_to_one)
        self.assertEqual(MensagemAtendimento._meta.get_field('texto').get_internal_type(), 'TextField')

    def test_central_de_ajuda_nao_aparece_no_menu(self):
        self.assertNotIn('Central de ajuda', self._read('aplicativo/templates/aplicativo/base.html'))

    def test_chat_e_comunicados_estao_no_dashboard(self):
        dashboard = self._read('aplicativo/templates/aplicativo/dashboard.html')
        self.assertIn('id="client-chat-fab"', dashboard)
        self.assertIn('id="client-notice-fab"', dashboard)

    def test_medicao_tem_duplo_clique_edicao_e_exclusao(self):
        js = self._read('aplicativo/static/aplicativo/js/medir-distancia.js')
        self.assertIn("map.on('dblclick', onDoubleClick)", js)
        self.assertIn("layer.editing.enable()", js)
        self.assertIn("measurements.removeLayer(layer)", js)
