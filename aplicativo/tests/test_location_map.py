from pathlib import Path

from django.test import SimpleTestCase
from django.urls import Resolver404, resolve


class ClientMapLocationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.map_js = (
            Path(__file__).resolve().parents[1]
            / 'static/aplicativo/js/mapa.js'
        ).read_text(encoding='utf-8')
        cls.free_mode_js = (
            Path(__file__).resolve().parents[1]
            / 'static/aplicativo/js/free-mode.js'
        ).read_text(encoding='utf-8')
        cls.dashboard_html = (
            Path(__file__).resolve().parents[1]
            / 'templates/aplicativo/dashboard.html'
        ).read_text(encoding='utf-8')

    def test_location_is_requested_once_per_page_and_uses_set_view_zoom_13(self):
        self.assertIn('navigator.geolocation.getCurrentPosition', self.map_js)
        self.assertIn('map.setView([latitude, longitude], 13)', self.map_js)
        self.assertNotIn('confronta_location_attempted', self.map_js)
        self.assertNotIn('sessionStorage', self.map_js)
        self.assertIn("const activeCar = Boolean(configElement && configElement.dataset.car)", self.map_js)
        self.assertNotIn('fetch(', self.map_js[self.map_js.index('function requestUserLocation'):self.map_js.index('function requestLocationAutomatically')])
        self.assertIn('let automaticLocationRequested = false', self.map_js)
        self.assertIn("showLocationMessage('Localizando sua região...')", self.map_js)
        self.assertIn("map.on('moveend zoomend', scheduleContext)", self.map_js)
        self.assertIn('if (current.key === lastKey || current.key === pendingKey) return;', self.map_js)

    def test_location_button_and_nonblocking_error_notice_are_present(self):
        self.assertIn("button.title = 'Minha localização'", self.map_js)
        self.assertIn('showLocationUnavailable();', self.map_js)
        self.assertNotIn('alert(', self.map_js)

    def test_free_contextual_geometry_opens_paywall_without_rendering_car_data(self):
        free_branch_start = self.map_js.index("if (configElement.dataset.freeMode === 'true')")
        free_branch_end = self.map_js.index('const props = feature.properties || {};', free_branch_start)
        free_branch = self.map_js[free_branch_start:free_branch_end]
        self.assertIn('CONFRONTA_SHOW_FREE_CAR_PAYWALL', free_branch)
        self.assertIn('return;', free_branch)
        for field in ('cod_imovel', 'municipio', 'situacao_car', 'Baixar CAR', 'Trabalhar no CAR'):
            self.assertNotIn(field, free_branch)
        self.assertIn('Conheça os detalhes deste imóvel', self.free_mode_js)
        self.assertIn('Contrate um plano do CONFRONTA para acessar os dados e análises deste CAR.', self.free_mode_js)
        self.assertIn('Ver planos', self.dashboard_html)

    def test_paid_contextual_popup_keeps_existing_details_and_actions(self):
        self.assertIn("['Código CAR', props.cod_imovel]", self.map_js)
        self.assertIn("['Município / UF'", self.map_js)
        self.assertIn("'Trabalhar no CAR'", self.map_js)
        self.assertIn("'Baixar CAR em KML'", self.map_js)

    def test_satellite_and_geographic_labels_are_default_toggleable_layers(self):
        self.assertIn('World_Imagery/MapServer/tile/{z}/{y}/{x}', self.map_js)
        self.assertIn('World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', self.map_js)
        self.assertIn("{ 'Nomes e localidades': referenceLabels }", self.map_js)
        self.assertIn("{ 'Satélite': satellite }", self.map_js)
        labels_layer = self.map_js[
            self.map_js.index('const referenceLabels = L.tileLayer('):
            self.map_js.index('referenceLabels.on(', self.map_js.index('const referenceLabels = L.tileLayer('))
        ]
        self.assertIn(').addTo(map);', labels_layer)
        self.assertIn("referencePane.style.zIndex = '250'", self.map_js)
        self.assertIn('referencePane.style.pointerEvents = \'none\'', self.map_js)

    def test_no_location_endpoint_was_added(self):
        with self.assertRaises(Resolver404):
            resolve('/mapa/localizacao/')
