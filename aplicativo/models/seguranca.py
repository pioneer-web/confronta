from django.db import models


class LimiteSeguranca(models.Model):
    """Contador persistente para proteção contra abuso e força bruta.

    A chave é sempre um HMAC SHA-256 gerado pela aplicação. IP, e-mail e outros
    identificadores brutos não são armazenados nesta tabela.
    """

    escopo = models.CharField(max_length=64)
    chave_hash = models.CharField(max_length=64)
    janela_iniciada_em = models.DateTimeField()
    tentativas = models.PositiveIntegerField(default=0)
    bloqueado_ate = models.DateTimeField(null=True, blank=True, db_index=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'limite de segurança'
        verbose_name_plural = 'limites de segurança'
        constraints = [
            models.UniqueConstraint(
                fields=['escopo', 'chave_hash'],
                name='uniq_limite_seguranca_escopo_chave',
            ),
        ]
        indexes = [
            models.Index(fields=['escopo', 'atualizado_em'], name='idx_seg_escopo_atualiz'),
        ]

    def __str__(self):
        return f'{self.escopo} — {self.tentativas}'
