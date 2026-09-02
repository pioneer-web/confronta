from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = (
        'Valida se o CONFRONTA SaaS está conectado ao dbconfronta compartilhado '
        'com o Manage CONFRONTA e mostra o estado das bases operacionais.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--target-only',
            action='store_true',
            help='Valida somente o banco alvo antes das migrations.',
        )

    def handle(self, *args, **options):
        expected = settings.CONFRONTA_EXPECTED_DB_NAME
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database(), current_user')
            current_db, current_user = cursor.fetchone()

            if current_db != expected:
                raise CommandError(
                    f'Banco incorreto: conectado a {current_db!r}, mas o SaaS espera '
                    f'{expected!r}. Sincronize o .env com o Manage CONFRONTA antes de iniciar.'
                )

            self.stdout.write(self.style.SUCCESS(
                f'Banco compartilhado OK: {current_db} (usuario: {current_user})'
            ))

            if options['target_only']:
                return

            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'postgis'")
            postgis = cursor.fetchone()
            self.stdout.write(f'PostGIS: {postgis[0] if postgis else "NAO INSTALADO"}')

            checks = (
                ('Registro de migrations', 'public.django_migrations'),
                ('Manage - lotes', 'public.administracao_loteimportacao'),
                ('Manage - camadas', 'public.administracao_camadaimportada'),
                ('SICAR operacional', 'dados_sicar.sicar_imoveis'),
            )
            for label, relation in checks:
                cursor.execute('SELECT to_regclass(%s)', [relation])
                exists = cursor.fetchone()[0] is not None
                marker = 'OK' if exists else '--'
                self.stdout.write(f'[{marker}] {label}: {relation}')

            cursor.execute("SELECT to_regclass('dados_sicar.sicar_imoveis')")
            if cursor.fetchone()[0] is not None:
                cursor.execute(
                    """
                    SELECT COALESCE(c.reltuples::bigint, 0)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'dados_sicar' AND c.relname = 'sicar_imoveis'
                    """
                )
                estimated = cursor.fetchone()
                if estimated:
                    self.stdout.write(
                        f'SICAR: aproximadamente {estimated[0]:,} registros segundo estatisticas do PostgreSQL.'
                    )
