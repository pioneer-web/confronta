from django.db import migrations, models
from django.db.models.functions import Lower
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('billing', '0002_billing_modality_privacy')]
    operations = [
        migrations.AddField('asaascheckout', 'parcelas_maximas_ofertadas', models.PositiveSmallIntegerField(default=1)),
        migrations.AddField('asaascheckout', 'parcelas_contratadas', models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField('asaascheckout', 'valor_desconto', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
        migrations.AddField('asaascheckout', 'valor_final', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
        migrations.AddField('asaascheckout', 'renovacao_de', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='checkouts_renovacao', to='billing.assinaturaasaas')),
        migrations.AddField('assinaturaasaas', 'parcelas_maximas_ofertadas', models.PositiveSmallIntegerField(default=1)),
        migrations.AddField('assinaturaasaas', 'parcelas_contratadas', models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField('assinaturaasaas', 'valor_desconto', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
        migrations.AddField('assinaturaasaas', 'valor_final', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
        migrations.AddField('assinaturaasaas', 'renovacao_status', models.CharField(choices=[('NONE', 'Nenhuma'), ('SCHEDULED', 'Agendada'), ('PENDING_REAUTHORIZATION', 'Aguardando reautorização'), ('CHECKOUT_CREATED', 'Checkout criado'), ('RENEWED', 'Renovada'), ('CANCELED', 'Cancelada')], db_index=True, default='NONE', max_length=32)),
        migrations.AddField('pagamentoasaas', 'asaas_installment_id', models.CharField(blank=True, db_index=True, max_length=100)),
        migrations.AddField('pagamentoasaas', 'acesso_aplicado', models.BooleanField(default=False)),
        migrations.CreateModel(
            name='Cupom',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo', models.CharField(max_length=80, unique=True)),
                ('descricao', models.CharField(blank=True, max_length=240)),
                ('tipo_desconto', models.CharField(choices=[('PERCENTUAL', 'Percentual'), ('VALOR_FIXO', 'Valor fixo')], max_length=16)),
                ('valor_desconto', models.DecimalField(decimal_places=2, max_digits=10)),
                ('ativo', models.BooleanField(db_index=True, default=True)),
                ('validade_inicio', models.DateTimeField(blank=True, null=True)),
                ('validade_fim', models.DateTimeField(blank=True, null=True)),
                ('limite_total_usos', models.PositiveIntegerField(blank=True, null=True)),
                ('quantidade_usos', models.PositiveIntegerField(default=0)),
                ('aplica_mensal', models.BooleanField(default=True)),
                ('aplica_anual', models.BooleanField(default=True)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['codigo']},
        ),
        migrations.AddConstraint('cupom', models.UniqueConstraint(Lower('codigo'), name='billing_cupom_codigo_ci_unico')),
        migrations.CreateModel(
            name='CupomUso',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('RESERVED', 'Reservado'), ('CONSUMED', 'Consumido'), ('RELEASED', 'Liberado')], db_index=True, default='RESERVED', max_length=12)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('consumido_em', models.DateTimeField(blank=True, null=True)),
                ('checkout', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='uso_cupom', to='billing.asaascheckout')),
                ('cupom', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='usos', to='billing.cupom')),
            ],
        ),
        migrations.AddConstraint('cupomuso', models.UniqueConstraint(fields=('cupom', 'checkout'), name='billing_cupom_checkout_unico')),
    ]
