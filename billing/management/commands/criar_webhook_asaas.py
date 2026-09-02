from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from billing.services.asaas import AsaasAPIError, AsaasClient, AsaasConfigurationError


EVENTOS = [
    'CHECKOUT_CREATED', 'CHECKOUT_PAID', 'CHECKOUT_CANCELED', 'CHECKOUT_EXPIRED',
    'SUBSCRIPTION_CREATED', 'SUBSCRIPTION_UPDATED', 'SUBSCRIPTION_INACTIVATED', 'SUBSCRIPTION_DELETED',
    'PAYMENT_CREATED', 'PAYMENT_UPDATED', 'PAYMENT_CONFIRMED', 'PAYMENT_RECEIVED', 'PAYMENT_OVERDUE',
    'PAYMENT_CREDIT_CARD_CAPTURE_REFUSED', 'PAYMENT_REPROVED_BY_RISK_ANALYSIS',
    'PAYMENT_REFUNDED', 'PAYMENT_CHARGEBACK_REQUESTED',
]


class Command(BaseCommand):
    help = 'Cria o webhook do CONFRONTA na conta Asaas configurada.'

    def add_arguments(self, parser):
        parser.add_argument('--url', required=True)
        parser.add_argument('--email', required=True)

    def handle(self, *args, **options):
        token = (getattr(settings, 'ASAAS_WEBHOOK_TOKEN', '') or '').strip()
        if len(token) < 32:
            raise CommandError('ASAAS_WEBHOOK_TOKEN deve ter pelo menos 32 caracteres.')

        payload = {
            'name': 'CONFRONTA Billing',
            'url': options['url'],
            'email': options['email'],
            'enabled': True,
            'interrupted': False,
            'apiVersion': 3,
            'authToken': token,
            'sendType': 'SEQUENTIALLY',
            'events': EVENTOS,
        }
        try:
            response = AsaasClient.from_settings().criar_webhook(payload)
        except (AsaasConfigurationError, AsaasAPIError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'Webhook criado: {response.get("id", response)}'))
