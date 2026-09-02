from django.conf import settings
from django.db import models
from administracao.constants import FonteDados


class Importacao(models.Model):
    class Status(models.TextChoices):
        RECEBIDO = 'RECEBIDO', 'Recebido'
        VALIDANDO = 'VALIDANDO', 'Validando segurança'
        REJEITADO_SEGURANCA = 'REJEITADO_SEGURANCA', 'Rejeitado por segurança'
        VALIDANDO_IDENTIDADE = 'VALIDANDO_IDENTIDADE', 'Validando identidade do dataset'
        REJEITADO_IDENTIDADE = 'REJEITADO_IDENTIDADE', 'Dataset não confirmado'
        VALIDANDO_GIS = 'VALIDANDO_GIS', 'Validando GIS'
        IMPORTANDO = 'IMPORTANDO', 'Importando'
        CONCLUIDO = 'CONCLUIDO', 'Concluído'
        IGNORADO_DUPLICADO = 'IGNORADO_DUPLICADO', 'Ignorado — já importado'
        SEM_ALTERACAO = 'SEM_ALTERACAO', 'Verificado — sem alteração'
        INTERROMPIDO = 'INTERROMPIDO', 'Interrompido'
        FALHOU = 'FALHOU', 'Falhou'

    fonte = models.CharField(max_length=20, choices=FonteDados.choices, db_index=True)
    dataset_slug = models.CharField(max_length=100, db_index=True, default='')
    dataset_label = models.CharField(max_length=255, blank=True)
    nome_arquivo_original = models.CharField(max_length=255)
    hash_sha256 = models.CharField(max_length=64, db_index=True)
    tamanho_bytes = models.BigIntegerField()
    administrador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='importacoes')
    status = models.CharField(max_length=40, choices=Status.choices, default=Status.RECEBIDO, db_index=True)
    identidade_status = models.CharField(max_length=30, default='PENDENTE', db_index=True)
    identidade_relatorio = models.JSONField(default=dict, blank=True)
    data_inicio = models.DateTimeField(auto_now_add=True)
    data_finalizacao = models.DateTimeField(null=True, blank=True)
    resultado = models.JSONField(default=dict, blank=True)
    motivo_rejeicao = models.TextField(blank=True)
    quarantine_path = models.CharField(max_length=500, blank=True)
    contexto = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-data_inicio']
        verbose_name = 'importação'
        verbose_name_plural = 'importações'
        indexes = [models.Index(fields=['fonte','dataset_slug','-data_inicio'], name='idx_import_dataset_data')]

    def __str__(self):
        return f'{self.fonte}/{self.dataset_slug} - {self.nome_arquivo_original} - {self.get_status_display()}'
