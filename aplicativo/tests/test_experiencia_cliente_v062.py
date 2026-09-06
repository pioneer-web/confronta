from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class ExperienciaClienteV062Tests(SimpleTestCase):
    def _read(self, relative):
        return (Path(settings.BASE_DIR) / relative).read_text(encoding='utf-8')

    def test_pos_pagamento_tem_assinatura_e_ambiente_de_trabalho(self):
        template = self._read('billing/templates/billing/_resultado_card.html')
        self.assertIn('Ver minha assinatura', template)
        self.assertIn('Entrar no ambiente de trabalho', template)
        self.assertIn('id="billing-workspace-action"', template)

    def test_assinatura_tem_quadro_de_dados_da_contratacao(self):
        template = self._read('aplicativo/templates/aplicativo/planos.html')
        self.assertIn('id="current-subscription-card-v062"', template)
        self.assertIn('Data da contratação', template)
        self.assertIn('Acesso liberado até', template)
        self.assertIn('Próximo vencimento', template)
        self.assertIn('dynamic-plan-choice-grid', template)

    def test_comunicados_abrem_em_drawer_lateral(self):
        template = self._read('aplicativo/templates/aplicativo/dashboard.html')
        self.assertIn('class="map-layer-drawer client-notice-drawer"', template)
        self.assertIn('id="client-notice-panel"', template)
        self.assertIn('Comunicado CONFRONTA', template)
        js = self._read('aplicativo/static/aplicativo/js/suporte-chat.js')
        self.assertIn("const noticeButton = document.getElementById('rail-comunicados')", js)
        self.assertIn("if (layerDrawer) layerDrawer.hidden = true;", js)
