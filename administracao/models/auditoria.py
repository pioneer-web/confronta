from django.conf import settings
from django.db import models


class Auditoria(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='auditorias')
    acao = models.CharField(max_length=100, db_index=True)
    entidade = models.CharField(max_length=100)
    identificador = models.CharField(max_length=255)
    detalhes = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'registro de auditoria'
        verbose_name_plural = 'registros de auditoria'

    def __str__(self):
        return f'{self.criado_em} - {self.acao} - {self.identificador}'
