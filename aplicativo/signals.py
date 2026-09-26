from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from aplicativo.permissions import SESSION_LOGOUT_LOCAL
from aplicativo.session_control import ativar_sessao_unica_cliente


@receiver(user_logged_in, dispatch_uid='aplicativo.google_single_client_session')
def ativar_sessao_google(sender, request, user, **kwargs):
    if request is None or not getattr(request, '_confronta_login_google', False):
        return
    if getattr(request, '_confronta_google_session_activated', False):
        return
    ativar_sessao_unica_cliente(request, user)
    request.session.pop(SESSION_LOGOUT_LOCAL, None)
    request._confronta_google_session_activated = True
