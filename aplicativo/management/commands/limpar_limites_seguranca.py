from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from aplicativo.models import LimiteSeguranca


class Command(BaseCommand):
    help = 'Remove contadores de segurança antigos que não estão mais bloqueando requisições.'

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=7)

    def handle(self, *args, **options):
        dias = max(1, options['dias'])
        corte = timezone.now() - timedelta(days=dias)
        qs = LimiteSeguranca.objects.filter(atualizado_em__lt=corte).filter(
            bloqueado_ate__isnull=True
        ) | LimiteSeguranca.objects.filter(atualizado_em__lt=corte, bloqueado_ate__lt=timezone.now())
        apagados, _ = qs.delete()
        self.stdout.write(self.style.SUCCESS(f'{apagados} registro(s) de limite removido(s).'))
