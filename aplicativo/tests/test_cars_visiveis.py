import json
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve, reverse

from aplicativo.repositories.territorial import RepositorioTerritorial
from aplicativo.session_keys import SESSION_CAR_ATUAL
from aplicativo.views.cars_visiveis import cars_visiveis


class CarsVisiveisTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.url = reverse('aplicativo:cars_visiveis')
        self.params = {'west': '-35.1', 'south': '-8.1', 'east': '-35.0', 'north': '-8.0', 'zoom': '12'}
        self.admin = SimpleNamespace(
            pk=42, is_authenticated=True, is_active=True, is_superuser=True,
        )

    def request(self, params=None, *, user=None, selected=None):
        request = self.factory.get(self.url, data=self.params if params is None else params)
        request.user = self.admin if user is None else user
        request.session = {SESSION_CAR_ATUAL: selected} if selected else {}
        return request

    def test_rota_existe_e_nega_acesso_sem_permissao(self):
        self.assertEqual(resolve(self.url).func.__name__, cars_visiveis.__name__)
        anonimo = SimpleNamespace(is_authenticated=False)
        with patch.object(RepositorioTerritorial, 'buscar_cars_no_bbox') as busca:
            self.assertEqual(cars_visiveis(self.request(user=anonimo)).status_code, 302)
            sem_plano = SimpleNamespace(
                is_authenticated=True, is_active=True, is_superuser=False,
                role='CLIENTE', Role=SimpleNamespace(ADMIN_TOTAL='ADMIN_TOTAL', ADMIN_JUNIOR='ADMIN_JUNIOR'),
                perfil_cliente=SimpleNamespace(ativo=True, acesso_vigente=True, plano='SEM_PLANO'),
            )
            self.assertEqual(cars_visiveis(self.request(user=sem_plano)).status_code, 403)
            busca.assert_not_called()

    def test_zoom_baixo_e_bbox_invalido_nao_consultam(self):
        invalidos = [
            {**self.params, 'zoom': '11.5'},
            {**self.params, 'zoom': 'nan'},
            {**self.params, 'west': '-181'},
            {**self.params, 'north': '91'},
            {**self.params, 'west': '-34'},
            {**self.params, 'east': '180'},
            {**self.params, 'east': 'infinity'},
            {key: value for key, value in self.params.items() if key != 'south'},
        ]
        with patch.object(RepositorioTerritorial, 'buscar_cars_no_bbox') as busca:
            for params in invalidos:
                with self.subTest(params=params):
                    self.assertEqual(cars_visiveis(self.request(params)).status_code, 400)
            busca.assert_not_called()

    def test_resposta_valida_e_car_da_sessao_excluido(self):
        feature = {'type': 'Feature', 'properties': {'cod_imovel': 'OUTRO'},
                   'geometry': {'type': 'Polygon', 'coordinates': []}}
        resultado = {'quantidade': 1, 'truncada': False, 'features': [feature]}
        with patch.object(RepositorioTerritorial, 'buscar_cars_no_bbox', return_value=resultado) as busca:
            response = cars_visiveis(self.request(selected='PE-CAR-ATUAL'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), resultado)
        busca.assert_called_once_with(-35.1, -8.1, -35.0, -8.0, car_excluido='PE-CAR-ATUAL')

    def test_limite_e_truncamento_no_repositorio(self):
        rows = [
            (f'CAR-{idx}', 'Recife', 'PE', 12, 'ATIVO', '{"type":"Polygon","coordinates":[]}')
            for idx in range(751)
        ]
        with patch.object(RepositorioTerritorial, '_camada_ativa', return_value=True), \
             patch.object(RepositorioTerritorial, '_table_srid', return_value=4326), \
             patch('aplicativo.repositories.territorial.connection') as db:
            cursor = db.cursor
            cursor.return_value.__enter__.return_value.fetchall.return_value = rows
            resultado = RepositorioTerritorial().buscar_cars_no_bbox(-35.1, -8.1, -35, -8, car_excluido='CAR-ATUAL')
            query, params = cursor.return_value.__enter__.return_value.execute.call_args.args

        self.assertEqual(resultado['quantidade'], 750)
        self.assertTrue(resultado['truncada'])
        self.assertEqual(len(resultado['features']), 750)
        self.assertEqual(set(resultado['features'][0]['properties']),
                         {'cod_imovel', 'municipio', 'uf', 'area_total_ha', 'situacao_car'})
        self.assertLess(str(query).index('&&'), str(query).index('ST_Intersects'))
        self.assertIn('MATERIALIZED', str(query))
        self.assertEqual(params[-1], 751)
        self.assertEqual(params[-3:-1], ['CAR-ATUAL', 'CAR-ATUAL'])

    def test_teto_de_requisicoes_por_usuario(self):
        with patch.object(RepositorioTerritorial, 'buscar_cars_no_bbox', return_value={
            'quantidade': 0, 'truncada': False, 'features': [],
        }) as busca:
            for _ in range(60):
                self.assertEqual(cars_visiveis(self.request()).status_code, 200)
            self.assertEqual(cars_visiveis(self.request()).status_code, 429)
            self.assertEqual(busca.call_count, 60)
