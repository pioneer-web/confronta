from django.test import TestCase
from django.urls import reverse

from administracao.models import User
from aplicativo.models import AtendimentoCliente, MensagemAtendimento


class AtendimentoManageV062Tests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin-v062@test.local',
            password='SenhaForte123!',
            role=User.Role.ADMIN_TOTAL,
        )
        self.cliente = User.objects.create_user(
            email='cliente-v062@test.local',
            password='SenhaForte123!',
            first_name='Cliente',
            last_name='Teste',
        )
        self.atendimento = AtendimentoCliente.objects.create(cliente=self.cliente)
        MensagemAtendimento.objects.create(
            atendimento=self.atendimento,
            autor=self.cliente,
            texto='Preciso de ajuda com uma consulta.',
        )
        self.client.force_login(self.admin)

    def test_detalhe_abre_sem_atendente_e_exibe_mensagem(self):
        response = self.client.get(
            reverse('administracao:atendimento_detalhe', kwargs={'pk': self.atendimento.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Preciso de ajuda com uma consulta.')
        self.assertContains(response, 'Não atribuído')

    def test_admin_responde_ao_cliente(self):
        response = self.client.post(
            reverse('administracao:atendimento_detalhe', kwargs={'pk': self.atendimento.pk}),
            {'acao': 'mensagem', 'texto': 'Olá, vamos verificar.'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            MensagemAtendimento.objects.filter(
                atendimento=self.atendimento,
                autor=self.admin,
                texto='Olá, vamos verificar.',
            ).exists()
        )
