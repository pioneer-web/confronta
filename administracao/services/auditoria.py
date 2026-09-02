from administracao.models import Auditoria


def registrar_auditoria(usuario, acao, entidade, identificador, detalhes=None):
    return Auditoria.objects.create(
        usuario=usuario,
        acao=acao,
        entidade=entidade,
        identificador=str(identificador),
        detalhes=detalhes or {},
    )
