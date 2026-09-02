from administracao.source_catalog import SOURCE_PROFILES


def administracao_context(request):
    """Contexto único do backoffice operacional e comercial do CONFRONTA."""
    sources = [
        source for source in SOURCE_PROFILES
        if source.is_importable and source.implementation != 'FUTURO'
    ]
    user = getattr(request, 'user', None)
    autenticado = bool(user and user.is_authenticated)
    is_superadmin = bool(autenticado and user.is_superuser)
    is_admin_total = bool(autenticado and getattr(user, 'role', None) == 'ADMIN_TOTAL')
    return {
        'manage_sidebar_sources': sources,
        'can_manage_commercial': bool(is_superadmin or is_admin_total),
        'can_manage_admins': is_superadmin,
        'can_manage_tables': bool(autenticado and getattr(user, 'can_manage_tables', False)),
    }


def manage_navigation(request):
    # Alias de compatibilidade para templates/configurações antigas do Manage.
    return administracao_context(request)
