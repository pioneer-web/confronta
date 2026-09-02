from django.conf import settings
from django.db import models
from administracao.constants import FonteDados


class Alerta(models.Model):
    class Tipo(models.TextChoices):
        TABELA_NAO_UTILIZADA = 'TABELA_NAO_UTILIZADA', 'Tabela não utilizada'
        ALTERACAO_ESTRUTURAL = 'ALTERACAO_ESTRUTURAL', 'Alteração estrutural'
        GEOMETRIA_CORRIGIDA = 'GEOMETRIA_CORRIGIDA', 'Geometrias corrigidas automaticamente'

    tipo = models.CharField(max_length=40, choices=Tipo.choices)
    fonte = models.CharField(max_length=20, choices=FonteDados.choices, db_index=True)
    camada = models.ForeignKey('administracao.CamadaImportada', on_delete=models.CASCADE, related_name='alertas')
    mensagem = models.TextField()
    ativo = models.BooleanField(default=True, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    resolvido_em = models.DateTimeField(null=True, blank=True)
    resolvido_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='alertas_resolvidos')

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'alerta'
        verbose_name_plural = 'alertas'

    def __str__(self):
        return f'{self.get_tipo_display()} - {self.camada}'
