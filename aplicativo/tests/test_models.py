from django.test import TestCase

from administracao.models import User
from aplicativo.models import PerfilCliente


class PerfilClienteTests(TestCase):
    def test_total_pode_desenhar_glebas(self):
        user = User.objects.create_user(email='cliente@test.local', password='SenhaForte123!')
        perfil = PerfilCliente.objects.create(usuario=user, plano=PerfilCliente.Plano.TOTAL)
        self.assertTrue(perfil.pode_desenhar_glebas)
        self.assertTrue(perfil.pode_consultar)

    def test_basico_nao_pode_desenhar_glebas(self):
        user = User.objects.create_user(email='basico@test.local', password='SenhaForte123!')
        perfil = PerfilCliente.objects.create(usuario=user, plano=PerfilCliente.Plano.BASICO)
        self.assertFalse(perfil.pode_desenhar_glebas)
        self.assertTrue(perfil.pode_consultar)

    def test_sem_plano_pode_logar_mas_nao_consultar(self):
        user = User.objects.create_user(email='semplano@test.local', password='SenhaForte123!')
        perfil = PerfilCliente.objects.create(usuario=user, plano=PerfilCliente.Plano.SEM_PLANO)
        self.assertFalse(perfil.possui_plano)
        self.assertFalse(perfil.pode_consultar)
        self.assertFalse(perfil.pode_desenhar_glebas)
