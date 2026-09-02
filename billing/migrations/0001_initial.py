import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('aplicativo', '0006_plano_confronta_lancamento'),
    ]

    operations = [
        migrations.CreateModel(
            name='AsaasCheckout',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('referencia', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('ciclo', models.CharField(choices=[('MONTHLY', 'Mensal'), ('YEARLY', 'Anual')], max_length=12)),
                ('valor', models.DecimalField(decimal_places=2, max_digits=10)),
                ('asaas_checkout_id', models.CharField(blank=True, max_length=100, null=True, unique=True)),
                ('asaas_customer_id', models.CharField(blank=True, max_length=100)),
                ('checkout_url', models.URLField(blank=True, max_length=600)),
                ('status', models.CharField(choices=[('CREATING', 'Criando'), ('ACTIVE', 'Aguardando pagamento'), ('PAID', 'Pago'), ('CANCELED', 'Cancelado'), ('EXPIRED', 'Expirado'), ('ERROR', 'Erro')], db_index=True, default='CREATING', max_length=20)),
                ('resposta_asaas', models.JSONField(blank=True, default=dict)),
                ('erro', models.TextField(blank=True)),
                ('expira_em', models.DateTimeField(blank=True, null=True)),
                ('pago_em', models.DateTimeField(blank=True, null=True)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('perfil', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='checkouts_asaas', to='aplicativo.perfilcliente')),
                ('plano', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='checkouts_asaas', to='aplicativo.planocomercial')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='checkouts_asaas', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-criado_em']},
        ),
        migrations.CreateModel(
            name='AssinaturaAsaas',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ciclo', models.CharField(choices=[('MONTHLY', 'Mensal'), ('YEARLY', 'Anual')], max_length=12)),
                ('valor', models.DecimalField(decimal_places=2, max_digits=10)),
                ('asaas_customer_id', models.CharField(blank=True, db_index=True, max_length=100)),
                ('asaas_subscription_id', models.CharField(blank=True, max_length=100, null=True, unique=True)),
                ('status', models.CharField(choices=[('PENDING', 'Pendente'), ('ACTIVE', 'Ativa'), ('PAST_DUE', 'Em atraso'), ('SUSPENDED', 'Suspensa'), ('CANCELED', 'Cancelada'), ('INACTIVE', 'Inativa')], db_index=True, default='PENDING', max_length=20)),
                ('atual', models.BooleanField(db_index=True, default=True)),
                ('proximo_vencimento', models.DateField(blank=True, null=True)),
                ('acesso_ate', models.DateField(blank=True, null=True)),
                ('cancelamento_solicitado', models.BooleanField(default=False)),
                ('iniciado_em', models.DateTimeField(default=django.utils.timezone.now)),
                ('encerrado_em', models.DateTimeField(blank=True, null=True)),
                ('ultimo_payload', models.JSONField(blank=True, default=dict)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('checkout_origem', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assinatura_gerada', to='billing.asaascheckout')),
                ('perfil', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assinaturas_asaas', to='aplicativo.perfilcliente')),
                ('plano', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assinaturas_asaas', to='aplicativo.planocomercial')),
            ],
            options={'ordering': ['-criado_em']},
        ),
        migrations.CreateModel(
            name='EventoWebhookAsaas',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_id', models.CharField(max_length=180, unique=True)),
                ('event_type', models.CharField(db_index=True, max_length=100)),
                ('payload', models.JSONField()),
                ('status', models.CharField(choices=[('RECEIVED', 'Recebido'), ('PROCESSED', 'Processado'), ('PENDING', 'Aguardando vínculo'), ('ERROR', 'Erro'), ('IGNORED', 'Ignorado')], db_index=True, default='RECEIVED', max_length=20)),
                ('tentativas', models.PositiveIntegerField(default=0)),
                ('erro', models.TextField(blank=True)),
                ('recebido_em', models.DateTimeField(auto_now_add=True)),
                ('processado_em', models.DateTimeField(blank=True, null=True)),
            ],
            options={'ordering': ['recebido_em']},
        ),
        migrations.CreateModel(
            name='PagamentoAsaas',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('asaas_payment_id', models.CharField(max_length=100, unique=True)),
                ('asaas_subscription_id', models.CharField(blank=True, db_index=True, max_length=100)),
                ('asaas_customer_id', models.CharField(blank=True, db_index=True, max_length=100)),
                ('status', models.CharField(blank=True, db_index=True, max_length=80)),
                ('forma_pagamento', models.CharField(blank=True, max_length=40)),
                ('valor', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('valor_liquido', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('vencimento', models.DateField(blank=True, null=True)),
                ('confirmacao', models.DateField(blank=True, null=True)),
                ('pagamento', models.DateField(blank=True, null=True)),
                ('invoice_url', models.URLField(blank=True, max_length=600)),
                ('ultimo_payload', models.JSONField(blank=True, default=dict)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('assinatura', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pagamentos', to='billing.assinaturaasaas')),
            ],
            options={'ordering': ['-vencimento', '-criado_em']},
        ),
        migrations.AddConstraint(
            model_name='assinaturaasaas',
            constraint=models.UniqueConstraint(condition=models.Q(('atual', True)), fields=('perfil',), name='billing_uma_assinatura_atual_por_perfil'),
        ),
        migrations.AddIndex(
            model_name='asaascheckout',
            index=models.Index(fields=['perfil', 'status'], name='billing_asa_perfil__791867_idx'),
        ),
        migrations.AddIndex(
            model_name='asaascheckout',
            index=models.Index(fields=['asaas_customer_id'], name='billing_asa_asaas_c_a23132_idx'),
        ),
        migrations.AddIndex(
            model_name='assinaturaasaas',
            index=models.Index(fields=['perfil', 'status', 'atual'], name='billing_ass_perfil__84d1be_idx'),
        ),
        migrations.AddIndex(
            model_name='assinaturaasaas',
            index=models.Index(fields=['asaas_customer_id', 'atual'], name='billing_ass_asaas_c_c3cbb4_idx'),
        ),
        migrations.AddIndex(
            model_name='eventowebhookasaas',
            index=models.Index(fields=['status', 'recebido_em'], name='billing_eve_status_04d550_idx'),
        ),
    ]
