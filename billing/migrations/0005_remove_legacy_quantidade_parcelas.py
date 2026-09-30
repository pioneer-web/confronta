from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('billing', '0004_billing_v2_backfill')]

    # Schema-only migration, deliberately separate from the 0004 data updates.
    operations = [
        migrations.RemoveField('asaascheckout', 'quantidade_parcelas'),
        migrations.RemoveField('assinaturaasaas', 'quantidade_parcelas'),
    ]
