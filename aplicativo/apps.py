from django.apps import AppConfig


class AplicativoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'aplicativo'
    verbose_name = 'CONFRONTA — Área Aplicativo'

    def ready(self):
        from aplicativo import signals  # noqa: F401
