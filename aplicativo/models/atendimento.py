from django.conf import settings
from django.db import models
from django.utils import timezone


class AtendimentoCliente(models.Model):
    class Status(models.TextChoices):
        ABERTO = 'ABERTO', 'Aberto'
        EM_ATENDIMENTO = 'EM_ATENDIMENTO', 'Em atendimento'
        ENCERRADO = 'ENCERRADO', 'Encerrado'

    cliente = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='atendimento_cliente',
    )
    atendente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='atendimentos_assumidos',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ABERTO, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    ultima_interacao_em = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-ultima_interacao_em', '-id']
        verbose_name = 'atendimento ao cliente'
        verbose_name_plural = 'atendimentos aos clientes'

    def __str__(self):
        return f'Atendimento Ã¢â‚¬â€ {self.cliente.get_full_name() or self.cliente.email}'


class MensagemAtendimento(models.Model):
    atendimento = models.ForeignKey(
        AtendimentoCliente,
        on_delete=models.CASCADE,
        related_name='mensagens',
    )
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='mensagens_atendimento',
    )
    texto = models.TextField()
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)
    lida_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['criado_em', 'id']
        indexes = [
            models.Index(fields=['atendimento', 'criado_em']),
            models.Index(fields=['atendimento', 'lida_em']),
        ]
        verbose_name = 'mensagem de atendimento'
        verbose_name_plural = 'mensagens de atendimento'

    def __str__(self):
        return f'Mensagem #{self.pk} Ã¢â‚¬â€ atendimento {self.atendimento_id}'
