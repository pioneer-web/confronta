from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('billing', '0003_coupons_renewals_parcels')]

    # Data-only migration. Keep all UPDATEs in this migration so its transaction
    # commits before 0005 performs ALTER TABLE / RemoveField operations.
    operations = [
        migrations.RunSQL(
            """
            UPDATE billing_asaascheckout
               SET parcelas_maximas_ofertadas = CASE
                   WHEN quantidade_parcelas > 0 THEN quantidade_parcelas
                   WHEN ciclo = 'YEARLY' AND modalidade = 'YEARLY_INSTALLMENT' THEN 6
                   ELSE 1
               END
             WHERE parcelas_maximas_ofertadas = 1
            """,
            migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            """
            UPDATE billing_assinaturaasaas
               SET parcelas_maximas_ofertadas = CASE
                   WHEN quantidade_parcelas > 0 THEN quantidade_parcelas
                   WHEN ciclo = 'YEARLY' AND modalidade = 'YEARLY_INSTALLMENT' THEN 6
                   ELSE 1
               END
             WHERE parcelas_maximas_ofertadas = 1
            """,
            migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            "UPDATE billing_asaascheckout SET valor_original = valor WHERE valor_original IS NULL",
            migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            "UPDATE billing_assinaturaasaas SET valor_original = valor WHERE valor_original IS NULL",
            migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            "UPDATE billing_asaascheckout SET valor_desconto = desconto WHERE valor_desconto = 0 AND desconto <> 0",
            migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            "UPDATE billing_assinaturaasaas SET valor_desconto = desconto WHERE valor_desconto = 0 AND desconto <> 0",
            migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            "UPDATE billing_asaascheckout SET valor_final = valor WHERE valor_final IS NULL",
            migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            "UPDATE billing_assinaturaasaas SET valor_final = valor WHERE valor_final IS NULL",
            migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            "UPDATE billing_assinaturaasaas SET renovacao_status = 'SCHEDULED' WHERE ciclo = 'YEARLY' AND modalidade = 'YEARLY_INSTALLMENT' AND renovacao_status = 'NONE'",
            migrations.RunSQL.noop,
        ),
    ]
