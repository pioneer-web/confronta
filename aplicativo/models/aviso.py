from django.conf import settings
from django.db import models


class AvisoCliente(models.Model):
    mensagem = models.TextField()
    ativo = models.BooleanField(default=True, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='avisos_clientes_criados',
    )

    class Meta:
        ordering = ['-criado_em', '-id']
        verbose_name = 'aviso aos clientes'
        verbose_name_plural = 'avisos aos clientes'

    def __str__(self):
        resumo = ' '.join((self.mensagem or '').split())
        return resumo[:80] or f'Aviso #{self.pk}'


class LeituraAvisoCliente(models.Model):
    aviso = models.ForeignKey(
        AvisoCliente,
        on_delete=models.CASCADE,
        related_name='leituras',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='leituras_avisos_clientes',
    )
    lido_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['aviso', 'usuario'],
                name='uniq_leitura_aviso_cliente_usuario',
            ),
        ]
        indexes = [
            models.Index(fields=['usuario', 'aviso'], name='idx_aviso_leitura_user'),
        ]
        ordering = ['-lido_em']
        verbose_name = 'leitura de aviso aos clientes'
        verbose_name_plural = 'leituras de avisos aos clientes'

    def __str__(self):
        return f'{self.usuario_id} leu aviso {self.aviso_id}'
