import os
from django.core.management.base import BaseCommand
from administracao.models import User


class Command(BaseCommand):
    help = 'Cria o Superadministrador inicial a partir das variáveis de ambiente, se ainda não existir.'

    def handle(self, *args, **options):
        email = os.getenv('DJANGO_SUPERUSER_EMAIL', '').strip().lower()
        password = os.getenv('DJANGO_SUPERUSER_PASSWORD', '')
        if not email or not password:
            self.stdout.write('Superadministrador automático não configurado; nenhuma conta foi criada.')
            return
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write('Superadministrador já existe; nenhuma alteração foi feita.')
            return
        User.objects.create_superuser(
            email=email,
            password=password,
            first_name=os.getenv('DJANGO_SUPERUSER_FIRST_NAME', 'Super'),
            last_name=os.getenv('DJANGO_SUPERUSER_LAST_NAME', 'Administrador'),
        )
        self.stdout.write(self.style.SUCCESS(f'Superadministrador inicial criado: {email}'))
