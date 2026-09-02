from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from administracao.models import User
from aplicativo.models import PerfilCliente
from aplicativo.session_keys import SESSION_CAR_ATUAL


class AplicativoRouteTests(TestCase):
    CAR = 'PE-2614105-9C74D4EF908C4BF4A177617BDC9C3D86'

    def setUp(self):
        self.basico = User.objects.create_user(email='basico@test.local', password='SenhaForte123!')
        PerfilCliente.objects.create(usuario=self.basico, plano=PerfilCliente.Plano.BASICO)

        self.total = User.objects.create_user(email='total.cliente@test.local', password='SenhaForte123!')
        PerfilCliente.objects.create(usuario=self.total, plano=PerfilCliente.Plano.TOTAL)

        self.sem_plano = User.objects.create_user(email='semplano@test.local', password='SenhaForte123!')
        PerfilCliente.objects.create(usuario=self.sem_plano, plano=PerfilCliente.Plano.SEM_PLANO)

        self.admin_total = User.objects.create_user(
            email='admin.total@test.local', password='SenhaForte123!',
            role=User.Role.ADMIN_TOTAL, is_staff=True,
        )

    def _consulta(self):
        return {
            'imovel': {
                'cod_imovel': self.CAR,
                'area_total_ha': 100.0,
                'uf': 'PE',
                'municipio': 'Sertânia',
                'modulos_fiscais': 2.0,
                'geometry': {'type': 'Polygon', 'coordinates': []},
            },
            'camadas': {},
            'analises_externas': {},
            'outros_cars': {
                'label': 'Sobreposição com outros CARs',
                'disponivel': True,
                'quantidade': 0,
                'features': [],
                'registros': [],
                'truncada': False,
                'motivo': '',
            },
            'alertas': {
                'tem_alerta': False,
                'resumo_mapa': '',
            },
        }

    def test_rota_publica_do_modulo_e_mapa(self):
        self.assertEqual(reverse('aplicativo:inicio'), '/mapa/')
        self.assertEqual(reverse('aplicativo:login'), '/mapa/login/')
        self.assertEqual(reverse('aplicativo:nova_consulta'), '/mapa/nova/')
        self.assertEqual(reverse('aplicativo:conta'), '/mapa/conta/')
        self.assertEqual(reverse('aplicativo:ajuda'), '/mapa/ajuda/')

    def test_area_mapa_redireciona_para_login(self):
        response = self.client.get(reverse('aplicativo:inicio'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/mapa/login/', response.url)

    def test_cliente_sem_plano_abre_area_com_cta(self):
        self.client.force_login(self.sem_plano)
        response = self.client.get(reverse('aplicativo:inicio'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Você ainda não possui um plano ativo')

    def test_nova_consulta_get_retorna_para_tela_principal(self):
        self.client.force_login(self.basico)
        response = self.client.get(reverse('aplicativo:nova_consulta'))
        self.assertRedirects(response, reverse('aplicativo:inicio'))

    def test_busca_invalida_nao_abre_tela_separada(self):
        self.client.force_login(self.basico)
        response = self.client.post(
            reverse('aplicativo:nova_consulta'),
            {'car': ''},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], reverse('aplicativo:inicio'))
        self.assertNotContains(response, 'new-query-drawer')
        self.assertContains(response, 'client-map-welcome')

    @patch('aplicativo.views.dashboard.ConsultaCarService.validar_existencia')
    def test_post_nova_consulta_grava_car_na_sessao_e_redireciona_para_url_limpa(self, validar):
        validar.return_value = self._consulta()['imovel']
        self.client.force_login(self.basico)

        response = self.client.post(reverse('aplicativo:nova_consulta'), {'car': self.CAR})

        self.assertRedirects(response, reverse('aplicativo:inicio'), fetch_redirect_response=False)
        self.assertEqual(self.client.session.get(SESSION_CAR_ATUAL), self.CAR)
        self.assertNotIn(self.CAR, response.url)
        self.assertNotIn('?', response.url)

    @patch('aplicativo.views.dashboard.ConsultaCarService.executar')
    def test_tela_operacional_nao_repete_formulario_de_consulta(self, executar):
        executar.return_value = self._consulta()
        self.client.force_login(self.total)
        session = self.client.session
        session[SESSION_CAR_ATUAL] = self.CAR
        session.save()

        response = self.client.get(reverse('aplicativo:inicio'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'territorial-screen')
        self.assertContains(response, 'client-topbar')
        self.assertContains(response, 'aria-label="Buscar CAR"', html=False)
        self.assertNotContains(response, 'Localizar imóvel pelo CAR')
        self.assertNotContains(response, 'Pode digitar com ou sem pontos e hífens')
        self.assertContains(response, self.CAR)

    @patch('aplicativo.views.dashboard.ConsultaCarService.executar')
    def test_query_string_car_e_ignorada_e_removida_da_url(self, executar):
        self.client.force_login(self.basico)
        response = self.client.get(reverse('aplicativo:inicio'), {'car': self.CAR})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('aplicativo:inicio'))
        executar.assert_not_called()

    @patch('aplicativo.views.dashboard.ConsultaCarService.executar')
    def test_exportacao_car_nao_expoe_car_na_rota(self, executar):
        self.client.force_login(self.basico)
        self.assertEqual(reverse('aplicativo:exportar_car_kml'), '/mapa/exportar/car/')
        self.assertNotIn(self.CAR, reverse('aplicativo:exportar_car_kml'))

    def test_logout_aplicativo_admin_preserva_painel_e_limpa_car_selecionado(self):
        self.client.force_login(self.admin_total)
        session = self.client.session
        session[SESSION_CAR_ATUAL] = self.CAR
        session.save()

        response = self.client.post(reverse('aplicativo:logout'))

        self.assertRedirects(response, reverse('aplicativo:login'))
        self.assertIn('_auth_user_id', self.client.session)
        self.assertNotIn(SESSION_CAR_ATUAL, self.client.session)
        painel = self.client.get(reverse('administracao:dashboard'))
        self.assertEqual(painel.status_code, 200)
