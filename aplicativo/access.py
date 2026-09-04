from dataclasses import dataclass

from aplicativo.models import PerfilCliente


@dataclass(frozen=True)
class AcessoAplicativo:
    """Representa o nível efetivo de acesso ao Módulo 2.

    Clientes comuns usam o plano persistido em ``PerfilCliente``. Contas
    administrativas existentes herdam o nível temporário aprovado para a fase
    inicial sem que seja necessário alterar os papéis do Módulo 1.
    """

    plano: str
    origem: str
    ativo: bool = True

    @property
    def possui_plano(self):
        return self.ativo and self.plano in {
            PerfilCliente.Plano.BASICO,
            PerfilCliente.Plano.TOTAL,
        }

    @property
    def pode_consultar(self):
        return self.possui_plano

    @property
    def pode_desenhar_glebas(self):
        return self.ativo and self.plano == PerfilCliente.Plano.TOTAL

    @property
    def eh_administrador(self):
        return self.origem in {'SUPERADMINISTRADOR', 'ADMIN_TOTAL', 'ADMIN_JUNIOR'}

    @property
    def eh_cliente(self):
        return self.origem == 'CLIENTE'

    def get_plano_display(self):
        return dict(PerfilCliente.Plano.choices).get(self.plano, self.plano)


def resolver_acesso_aplicativo(user):
    """Resolve o acesso efetivo do usuário ao Módulo 2.

    Regra temporária aprovada:
    - Superadministrador: Total;
    - Administrador Total: Total;
    - Administrador Júnior: Básico;
    - Cliente comum: plano de ``PerfilCliente`` (inclusive Sem plano).
    """

    if not getattr(user, 'is_authenticated', False):
        return None
    if not getattr(user, 'is_active', False):
        return None

    if getattr(user, 'is_superuser', False):
        return AcessoAplicativo(
            plano=PerfilCliente.Plano.TOTAL,
            origem='SUPERADMINISTRADOR',
        )

    role = getattr(user, 'role', None)
    role_class = getattr(user, 'Role', None)
    if role_class is not None and role == role_class.ADMIN_TOTAL:
        return AcessoAplicativo(
            plano=PerfilCliente.Plano.TOTAL,
            origem='ADMIN_TOTAL',
        )
    if role_class is not None and role == role_class.ADMIN_JUNIOR:
        return AcessoAplicativo(
            plano=PerfilCliente.Plano.BASICO,
            origem='ADMIN_JUNIOR',
        )

    try:
        perfil = user.perfil_cliente
    except PerfilCliente.DoesNotExist:
        return None

    if not perfil.ativo:
        return None

    return AcessoAplicativo(
        plano=perfil.plano,
        origem='CLIENTE',
        ativo=perfil.acesso_vigente,
    )
