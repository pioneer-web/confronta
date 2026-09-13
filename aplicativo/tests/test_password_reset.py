from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='CONFRONTA <nao-responda@confronta.test>',
)
class PasswordResetTests(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            email='cliente@confronta.test',
            password='SenhaAtual123!',
            is_active=True,
        )

    def test_login_exibe_esqueci_minha_senha(self):
        response = self.client.get(reverse('aplicativo:login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Esqueci minha senha')
        self.assertContains(
            response,
            reverse('aplicativo:password_reset')
        )

    def test_email_cadastrado_recebe_link(self):
        response = self.client.post(
            reverse('aplicativo:password_reset'),
            {'email': self.user.email},
        )

        self.assertRedirects(
            response,
            reverse('aplicativo:password_reset_done'),
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(
            'Recuperação de senha',
            mail.outbox[0].subject,
        )

    def test_email_inexistente_nao_revela_conta(self):
        response = self.client.post(
            reverse('aplicativo:password_reset'),
            {'email': 'naoexiste@confronta.test'},
        )

        self.assertRedirects(
            response,
            reverse('aplicativo:password_reset_done'),
        )

        self.assertEqual(len(mail.outbox), 0)

    def test_token_permite_definir_nova_senha(self):
        uid = urlsafe_base64_encode(
            force_bytes(self.user.pk)
        )

        token = default_token_generator.make_token(
            self.user
        )

        response = self.client.get(
            reverse(
                'aplicativo:password_reset_confirm',
                kwargs={
                    'uidb64': uid,
                    'token': token,
                },
            )
        )

        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            response['Location'],
            {
                'new_password1': 'NovaSenhaSegura456!',
                'new_password2': 'NovaSenhaSegura456!',
            },
        )

        self.assertRedirects(
            response,
            reverse('aplicativo:password_reset_complete'),
        )

        self.user.refresh_from_db()

        self.assertTrue(
            self.user.check_password(
                'NovaSenhaSegura456!'
            )
        )
