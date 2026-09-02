import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from aplicativo.models import PerfilCliente, PlanoComercial


class AsaasCheckout(models.Model):
    class Ciclo(models.TextChoices):
        MONTHLY = 'MONTHLY', 'Mensal'
        YEARLY = 'YEARLY', 'Anual'

    class Status(models.TextChoices):
        CREATING = 'CREATING', 'Criando'
        ACTIVE = 'ACTIVE', 'Aguardando pagamento'
        PAID = 'PAID', 'Pago'
        CANCELED = 'CANCELED', 'Cancelado'
        EXPIRED = 'EXPIRED', 'Expirado'
        ERROR = 'ERROR', 'Erro'

    referencia = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='checkouts_asaas')
    perfil = models.ForeignKey(PerfilCliente, on_delete=models.CASCADE, related_name='checkouts_asaas')
    plano = models.ForeignKey(PlanoComercial, on_delete=models.PROTECT, related_name='checkouts_asaas')
    ciclo = models.CharField(max_length=12, choices=Ciclo.choices)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    asaas_checkout_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    asaas_customer_id = models.CharField(max_length=100, blank=True)
    checkout_url = models.URLField(max_length=600, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATING, db_index=True)
    resposta_asaas = models.JSONField(default=dict, blank=True)
    erro = models.TextField(blank=True)
    expira_em = models.DateTimeField(null=True, blank=True)
    pago_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-criado_em']
        indexes = [
            models.Index(fields=['perfil', 'status'], name='billing_asa_perfil__791867_idx'),
            models.Index(fields=['asaas_customer_id'], name='billing_asa_asaas_c_a23132_idx'),
        ]

    def __str__(self):
        return f'{self.perfil_id} · {self.get_ciclo_display()} · {self.get_status_display()}'


class AssinaturaAsaas(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pendente'
        ACTIVE = 'ACTIVE', 'Ativa'
        PAST_DUE = 'PAST_DUE', 'Em atraso'
        SUSPENDED = 'SUSPENDED', 'Suspensa'
        CANCELED = 'CANCELED', 'Cancelada'
        INACTIVE = 'INACTIVE', 'Inativa'

    perfil = models.ForeignKey(PerfilCliente, on_delete=models.CASCADE, related_name='assinaturas_asaas')
    plano = models.ForeignKey(PlanoComercial, on_delete=models.PROTECT, related_name='assinaturas_asaas')
    checkout_origem = models.OneToOneField(AsaasCheckout, on_delete=models.SET_NULL, null=True, blank=True, related_name='assinatura_gerada')
    ciclo = models.CharField(max_length=12, choices=AsaasCheckout.Ciclo.choices)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    asaas_customer_id = models.CharField(max_length=100, blank=True, db_index=True)
    asaas_subscription_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    atual = models.BooleanField(default=True, db_index=True)
    proximo_vencimento = models.DateField(null=True, blank=True)
    acesso_ate = models.DateField(null=True, blank=True)
    cancelamento_solicitado = models.BooleanField(default=False)
    iniciado_em = models.DateTimeField(default=timezone.now)
    encerrado_em = models.DateTimeField(null=True, blank=True)
    ultimo_payload = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-criado_em']
        constraints = [
            models.UniqueConstraint(
                fields=['perfil'],
                condition=Q(atual=True),
                name='billing_uma_assinatura_atual_por_perfil',
            ),
        ]
        indexes = [
            models.Index(fields=['perfil', 'status', 'atual'], name='billing_ass_perfil__84d1be_idx'),
            models.Index(fields=['asaas_customer_id', 'atual'], name='billing_ass_asaas_c_c3cbb4_idx'),
        ]

    def __str__(self):
        return f'{self.perfil_id} · {self.get_ciclo_display()} · {self.get_status_display()}'


class PagamentoAsaas(models.Model):
    assinatura = models.ForeignKey(AssinaturaAsaas, on_delete=models.SET_NULL, null=True, blank=True, related_name='pagamentos')
    asaas_payment_id = models.CharField(max_length=100, unique=True)
    asaas_subscription_id = models.CharField(max_length=100, blank=True, db_index=True)
    asaas_customer_id = models.CharField(max_length=100, blank=True, db_index=True)
    status = models.CharField(max_length=80, blank=True, db_index=True)
    forma_pagamento = models.CharField(max_length=40, blank=True)
    valor = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    valor_liquido = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    vencimento = models.DateField(null=True, blank=True)
    confirmacao = models.DateField(null=True, blank=True)
    pagamento = models.DateField(null=True, blank=True)
    invoice_url = models.URLField(max_length=600, blank=True)
    ultimo_payload = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-vencimento', '-criado_em']

    def __str__(self):
        return f'{self.asaas_payment_id} · {self.status}'


class EventoWebhookAsaas(models.Model):
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', 'Recebido'
        PROCESSED = 'PROCESSED', 'Processado'
        PENDING = 'PENDING', 'Aguardando vínculo'
        ERROR = 'ERROR', 'Erro'
        IGNORED = 'IGNORED', 'Ignorado'

    event_id = models.CharField(max_length=180, unique=True)
    event_type = models.CharField(max_length=100, db_index=True)
    payload = models.JSONField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED, db_index=True)
    tentativas = models.PositiveIntegerField(default=0)
    erro = models.TextField(blank=True)
    recebido_em = models.DateTimeField(auto_now_add=True)
    processado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['recebido_em']
        indexes = [models.Index(fields=['status', 'recebido_em'], name='billing_eve_status_04d550_idx')]

    def __str__(self):
        return f'{self.event_type} · {self.event_id}'
