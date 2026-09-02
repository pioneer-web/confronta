import time

from django.core.management.base import BaseCommand

from billing.models import EventoWebhookAsaas
from billing.services.webhooks import processar_evento


class Command(BaseCommand):
    help = 'Processa a fila persistida de Webhooks Asaas.'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true')
        parser.add_argument('--interval', type=int, default=3)
        parser.add_argument('--limit', type=int, default=100)

    def _rodada(self, limit):
        eventos = list(
            EventoWebhookAsaas.objects
            .filter(status__in=[
                EventoWebhookAsaas.Status.RECEIVED,
                EventoWebhookAsaas.Status.PENDING,
                EventoWebhookAsaas.Status.ERROR,
            ])
            .order_by('recebido_em')[:limit]
        )
        for evento in eventos:
            # Erros permanentes não devem gerar loop agressivo infinito.
            if evento.status == EventoWebhookAsaas.Status.ERROR and evento.tentativas >= 10:
                continue
            try:
                processar_evento(evento)
            except Exception as exc:
                self.stderr.write(f'{evento.event_id}: {exc}')
        return len(eventos)

    def handle(self, *args, **options):
        while True:
            self._rodada(options['limit'])
            if not options['loop']:
                break
            time.sleep(max(1, options['interval']))
