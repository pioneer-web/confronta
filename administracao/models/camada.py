from django.db import models
from administracao.constants import FonteDados


class CamadaImportada(models.Model):
    class Status(models.TextChoices):
        ATIVA = 'ATIVA', 'Ativa'
        NAO_ENCONTRADA = 'NAO_ENCONTRADA', 'Não encontrada'
        PENDENTE_REVISAO = 'PENDENTE_REVISAO', 'Pendente de revisão'
        REMOVIDA = 'REMOVIDA', 'Removida'

    fonte = models.CharField(max_length=20, choices=FonteDados.choices, db_index=True)
    dataset_slug = models.CharField(max_length=100, db_index=True, default='')
    nome_original = models.CharField(max_length=255)
    schema_banco = models.CharField(max_length=63)
    nome_tabela = models.CharField(max_length=63)
    tabela_raw = models.CharField(max_length=63, blank=True)
    tipo_geometria = models.CharField(max_length=100, blank=True)
    srid = models.IntegerField(null=True, blank=True)
    assinatura_estrutura = models.CharField(max_length=64, db_index=True)
    primeira_importacao = models.DateTimeField()
    ultima_importacao = models.DateTimeField()
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.ATIVA, db_index=True)
    data_sem_uso = models.DateTimeField(null=True, blank=True)
    ultima_importacao_ref = models.ForeignKey('administracao.Importacao', null=True, blank=True, on_delete=models.SET_NULL, related_name='camadas')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['fonte','dataset_slug','schema_banco','nome_tabela'], name='uniq_camada_dataset_tabela')]
        ordering = ['fonte','dataset_slug','nome_tabela']
        verbose_name = 'camada importada'
        verbose_name_plural = 'camadas importadas'

    def __str__(self):
        return f'{self.fonte}/{self.dataset_slug}: {self.schema_banco}.{self.nome_tabela}'
