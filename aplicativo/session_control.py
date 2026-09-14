import secrets

from django.utils import timezone

from aplicativo.access import resolver_acesso_aplicativo
from aplicativo.models import PerfilCliente


SESSION_CLIENT_TOKEN = 'confronta_client_session_token'


def _perfil_cliente_comum(user):
    acesso = resolver_acesso_aplicativo(user)

    if acesso is None or not acesso.eh_cliente:
        return None

    try:
        return user.perfil_cliente
    except PerfilCliente.DoesNotExist:
        return None


def ativar_sessao_unica_cliente(request, user):
    """Transforma esta sessão na única sessão válida do cliente."""

    perfil = _perfil_cliente_comum(user)
    if perfil is None:
        return

    token = secrets.token_urlsafe(32)

    request.session[SESSION_CLIENT_TOKEN] = token

    PerfilCliente.objects.filter(pk=perfil.pk).update(
        token_sessao_ativa=token,
        sessao_ativa_em=timezone.now(),
    )


def validar_sessao_unica_cliente(request):
    """Retorna False quando a sessão foi substituída por outro login."""

    perfil = _perfil_cliente_comum(request.user)
    if perfil is None:
        return True

    token_banco = perfil.token_sessao_ativa or ''
    token_sessao = request.session.get(SESSION_CLIENT_TOKEN, '')

    # Compatibilidade com clientes que já estavam logados
    # antes da implantação desta funcionalidade.
    if not token_banco:
        if not token_sessao:
            token_sessao = secrets.token_urlsafe(32)
            request.session[SESSION_CLIENT_TOKEN] = token_sessao

        PerfilCliente.objects.filter(
            pk=perfil.pk,
            token_sessao_ativa='',
        ).update(
            token_sessao_ativa=token_sessao,
            sessao_ativa_em=timezone.now(),
        )

        perfil.refresh_from_db(
            fields=['token_sessao_ativa', 'sessao_ativa_em']
        )
        token_banco = perfil.token_sessao_ativa or ''

    if not token_sessao or not token_banco:
        return False

    return secrets.compare_digest(token_sessao, token_banco)


def desativar_sessao_unica_cliente(request, user):
    """Limpa o controle somente se esta for a sessão ativa atual."""

    perfil = _perfil_cliente_comum(user)
    if perfil is None:
        return

    token = request.session.get(SESSION_CLIENT_TOKEN, '')

    if token:
        PerfilCliente.objects.filter(
            pk=perfil.pk,
            token_sessao_ativa=token,
        ).update(
            token_sessao_ativa='',
            sessao_ativa_em=None,
        )

    request.session.pop(SESSION_CLIENT_TOKEN, None)
