from django.core.management.base import BaseCommand
from django.db import transaction
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

from billing.models import AsaasCheckout, AssinaturaAsaas


class Command(BaseCommand):
    help = 'Marca vencimentos anuais parcelados como renovação pendente, sem cobrar automaticamente.'

    def handle(self, *args, **options):
        hoje = timezone.localdate()
        limite = hoje + timedelta(days=max(0, int(getattr(settings, 'BILLING_RENEWAL_NOTICE_DAYS', 15))))
        ids = AssinaturaAsaas.objects.filter(
            atual=True,
            ciclo=AsaasCheckout.Ciclo.YEARLY,
            modalidade=AsaasCheckout.Modalidade.YEARLY_INSTALLMENT,
            renovacao_status__in=[AssinaturaAsaas.RenovacaoStatus.NONE, AssinaturaAsaas.RenovacaoStatus.SCHEDULED],
            acesso_ate__lte=limite,
        ).values_list('pk', flat=True)
        total = 0
        for assinatura_id in ids.iterator():
            with transaction.atomic():
                assinatura = AssinaturaAsaas.objects.select_for_update().filter(pk=assinatura_id, renovacao_status__in=[AssinaturaAsaas.RenovacaoStatus.NONE, AssinaturaAsaas.RenovacaoStatus.SCHEDULED]).first()
                if assinatura is None:
                    continue
                assinatura.renovacao_pendente = True
                assinatura.renovacao_status = AssinaturaAsaas.RenovacaoStatus.PENDING_REAUTHORIZATION
                assinatura.save(update_fields=['renovacao_pendente', 'renovacao_status', 'atualizado_em'])
                total += 1
        self.stdout.write(f'{total} renovação(ões) anual(is) marcada(s) como pendente(s).')
