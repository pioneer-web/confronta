from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from administracao.models import User
from aplicativo.models import PerfilCliente, PlanoComercial
from aplicativo.permissions import SESSION_LOGOUT_LOCAL
from aplicativo.session_control import ativar_sessao_unica_cliente


class ClienteAccountAdapter(DefaultAccountAdapter):
    def add_message(
        self,
        request,
        level,
        message_template=None,
        message_context=None,
        extra_tags='',
        message=None,
    ):
        # Não exibe o toast de login do allauth na dashboard. Filtra somente
        # essa mensagem; avisos e erros continuam usando o adapter padrão.
        if (
            level == messages.SUCCESS
            and message_template == 'account/messages/logged_in.txt'
        ):
            return
        return super().add_message(
            request,
            level,
            message_template=message_template,
            message_context=message_context,
            extra_tags=extra_tags,
            message=message,
        )

    def post_login(self, request, user, **kwargs):
        if getattr(request, '_confronta_login_google', False):
            # A URL é uma rota interna fixa: ignora qualquer next recebido.
            kwargs['redirect_url'] = reverse('aplicativo:inicio')
        response = super().post_login(request, user, **kwargs)
        if (
            getattr(request, '_confronta_login_google', False)
            and not getattr(request, '_confronta_google_session_activated', False)
        ):
            ativar_sessao_unica_cliente(request, user)
            request.session.pop(SESSION_LOGOUT_LOCAL, None)
            request._confronta_google_session_activated = True
        return response


class ClienteSocialAccountAdapter(DefaultSocialAccountAdapter):
    erro_conta_admin = 'Esta conta não pode utilizar o acesso Google pela Área do Cliente.'

    def pre_social_login(self, request, sociallogin):
        if sociallogin.account.provider != 'google':
            return super().pre_social_login(request, sociallogin)

        email = (sociallogin.account.extra_data.get('email') or '').strip().lower()
        emails_verificados = [
            item.email.strip().lower()
            for item in sociallogin.email_addresses
            if item.verified and item.email
        ]
        if not email or email not in emails_verificados:
            messages.error(request, 'Não foi possível confirmar o e-mail da sua conta Google.')
            raise ImmediateHttpResponse(redirect('aplicativo:login'))

        usuario_existente = User.objects.filter(email__iexact=email).first()
        if usuario_existente and (
            usuario_existente.is_staff
            or usuario_existente.is_superuser
            or usuario_existente.role in {User.Role.ADMIN_TOTAL, User.Role.ADMIN_JUNIOR}
        ):
            messages.error(request, self.erro_conta_admin)
            raise ImmediateHttpResponse(redirect('aplicativo:login'))

        if usuario_existente:
            # Explicit connect avoids allauth's email-authentication flow,
            # which can wipe a local password before linking the account.
            with transaction.atomic():
                sociallogin.connect(request, usuario_existente)

        # Used only by the customer session and redirect adapters after the
        # verified Google identity has passed the checks above.
        request._confronta_login_google = True

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra = sociallogin.account.extra_data
        user.email = (extra.get('email') or data.get('email') or '').strip().lower()
        user.first_name = extra.get('given_name') or data.get('first_name') or ''
        user.last_name = extra.get('family_name') or data.get('last_name') or ''
        return user

    def save_user(self, request, sociallogin, form=None):
        with transaction.atomic():
            user = super().save_user(request, sociallogin, form)
            user.set_unusable_password()
            user.is_active = True
            user.is_staff = False
            user.is_superuser = False
            user.role = None
            user.save(update_fields=['password', 'is_active', 'is_staff', 'is_superuser', 'role'])

            plano = PlanoComercial.objects.filter(slug='confronta', ativo=True).first()
            defaults = {
                'telefone': '',
                'plano': PerfilCliente.Plano.SEM_PLANO,
                'ativo': True,
                'renovacao_automatica': False,
            }
            if plano is not None:
                defaults.update(
                    plano_desejado=plano.nivel_acesso,
                    plano_desejado_comercial=plano,
                )
            PerfilCliente.objects.get_or_create(usuario=user, defaults=defaults)
        return user
