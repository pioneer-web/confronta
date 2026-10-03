from django.db import migrations, models
from django.db.models import Q


def scrub_payloads(apps, schema_editor):
    Checkout = apps.get_model('billing', 'AsaasCheckout')
    Subscription = apps.get_model('billing', 'AssinaturaAsaas')
    Payment = apps.get_model('billing', 'PagamentoAsaas')
    Event = apps.get_model('billing', 'EventoWebhookAsaas')
    response_fields = {'id', 'link', 'status', 'externalReference', 'expiresAt', 'installmentCount', 'installmentId', 'installment'}
    event_fields = {
        'checkout': {'id', 'customer', 'status', 'externalReference', 'installmentCount'},
        'subscription': {'id', 'customer', 'status', 'nextDueDate', 'cycle', 'value'},
        'payment': {'id', 'subscription', 'customer', 'status', 'billingType', 'value', 'netValue', 'dueDate', 'confirmedDate', 'paymentDate', 'invoiceUrl', 'installment', 'installmentId', 'installmentCount'},
    }
    def only(data, fields):
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if k in fields and isinstance(v, (str, int, float, bool, type(None)))}
    def event_only(data):
        if not isinstance(data, dict):
            return {}
        result = {k: v for k, v in data.items() if k in {'id', 'event', 'dateCreated'} and isinstance(v, (str, int, float, bool, type(None)))}
        for key, fields in event_fields.items():
            if key in data:
                result[key] = only(data[key], fields)
                if key == 'checkout' and isinstance(data[key], dict):
                    for nested in ('subscription', 'payment'):
                        if isinstance(data[key].get(nested), dict):
                            result[key][nested] = only(data[key][nested], event_fields[nested])
                if key == 'payment' and isinstance(data[key], dict) and isinstance(data[key].get('installment'), dict):
                    result[key]['installment'] = only(data[key]['installment'], {'id', 'status', 'value', 'installmentCount'})
        return result
    for obj in Checkout.objects.all().iterator():
        original = obj.resposta_asaas
        obj.resposta_asaas = only(original, response_fields)
        if isinstance(original, dict) and isinstance(original.get('installment'), dict):
            obj.resposta_asaas['installment'] = only(original['installment'], {'id', 'status', 'value', 'installmentCount'})
        obj.save(update_fields=['resposta_asaas'])
    for model, field in ((Subscription, 'ultimo_payload'), (Payment, 'ultimo_payload')):
        for obj in model.objects.all().iterator():
            kind = 'subscription' if model is Subscription else 'payment'
            obj.ultimo_payload = only(obj.ultimo_payload, event_fields[kind])
            obj.save(update_fields=[field])
    for obj in Event.objects.all().iterator():
        obj.payload = event_only(obj.payload)
        obj.save(update_fields=['payload'])


class Migration(migrations.Migration):
    atomic = False

    dependencies = [('billing', '0001_initial')]

    operations = [
        migrations.AddField('asaascheckout', 'modalidade', models.CharField(choices=[('MONTHLY', 'Mensal recorrente'), ('YEARLY_CASH', 'Anual à vista recorrente'), ('YEARLY_INSTALLMENT', 'Anual parcelado')], default='MONTHLY', max_length=24)),
        migrations.AddField('asaascheckout', 'quantidade_parcelas', models.PositiveSmallIntegerField(default=1)),
        migrations.AddField('asaascheckout', 'valor_original', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
        migrations.AddField('asaascheckout', 'desconto', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
        migrations.AddField('asaascheckout', 'cupom', models.CharField(blank=True, max_length=80)),
        migrations.AddField('assinaturaasaas', 'modalidade', models.CharField(choices=[('MONTHLY', 'Mensal recorrente'), ('YEARLY_CASH', 'Anual à vista recorrente'), ('YEARLY_INSTALLMENT', 'Anual parcelado')], default='MONTHLY', max_length=24)),
        migrations.AddField('assinaturaasaas', 'quantidade_parcelas', models.PositiveSmallIntegerField(default=1)),
        migrations.AddField('assinaturaasaas', 'valor_original', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
        migrations.AddField('assinaturaasaas', 'desconto', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
        migrations.AddField('assinaturaasaas', 'cupom', models.CharField(blank=True, max_length=80)),
        migrations.AddField('assinaturaasaas', 'renovacao_pendente', models.BooleanField(db_index=True, default=False)),
        migrations.RunPython(scrub_payloads, migrations.RunPython.noop),
        migrations.RunSQL("UPDATE billing_asaascheckout SET valor_original = valor WHERE valor_original IS NULL", migrations.RunSQL.noop),
        migrations.RunSQL("UPDATE billing_assinaturaasaas SET valor_original = valor WHERE valor_original IS NULL", migrations.RunSQL.noop),
        migrations.RunSQL("UPDATE billing_asaascheckout SET modalidade = 'YEARLY_INSTALLMENT', quantidade_parcelas = 6 WHERE ciclo = 'YEARLY'", migrations.RunSQL.noop),
        migrations.RunSQL("UPDATE billing_assinaturaasaas SET modalidade = CASE WHEN asaas_subscription_id IS NOT NULL THEN 'YEARLY_CASH' ELSE 'YEARLY_INSTALLMENT' END, quantidade_parcelas = CASE WHEN asaas_subscription_id IS NOT NULL THEN 1 ELSE 6 END WHERE ciclo = 'YEARLY'", migrations.RunSQL.noop),
    ]
