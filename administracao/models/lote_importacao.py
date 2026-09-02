from django.conf import settings
from django.db import models
from administracao.constants import FonteDados


class LoteImportacao(models.Model):
    class Status(models.TextChoices):
        RECEBIDO = 'RECEBIDO', 'Recebido'
        PREPARANDO = 'PREPARANDO', 'Preparando fila'
        ANALISANDO = 'ANALISANDO', 'Analisando arquivos'
        AGUARDANDO_CONFIRMACAO = 'AGUARDANDO_CONFIRMACAO', 'Aguardando confirmação'
        PROCESSANDO = 'PROCESSANDO', 'Importando alterações'
        INTERROMPENDO = 'INTERROMPENDO', 'Interrupção solicitada'
        INTERROMPIDO = 'INTERROMPIDO', 'Interrompido'
        CONCLUIDO = 'CONCLUIDO', 'Concluído'
        CONCLUIDO_COM_PENDENCIAS = 'CONCLUIDO_COM_PENDENCIAS', 'Concluído com pendências'
        FALHOU = 'FALHOU', 'Falhou'

    fonte = models.CharField(max_length=20, choices=FonteDados.choices, db_index=True)
    nome_arquivo_original = models.CharField(max_length=255)
    hash_sha256 = models.CharField(max_length=64, db_index=True)
    tamanho_bytes = models.BigIntegerField()
    administrador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='lotes_importacao',
    )
    status = models.CharField(max_length=40, choices=Status.choices, default=Status.RECEBIDO, db_index=True)
    data_inicio = models.DateTimeField(auto_now_add=True)
    data_finalizacao = models.DateTimeField(null=True, blank=True)
    resultado = models.JSONField(default=dict, blank=True)
    motivo_falha = models.TextField(blank=True)
    quarantine_path = models.CharField(max_length=500, blank=True)
    extracted_path = models.CharField(max_length=500, blank=True)
    oculto_painel = models.BooleanField(default=False, db_index=True)
    removido_painel_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-data_inicio']
        verbose_name = 'lote de importação'
        verbose_name_plural = 'lotes de importação'

    def __str__(self):
        return f'{self.get_fonte_display()} - lote #{self.pk} - {self.get_status_display()}'

    @property
    def pode_interromper(self):
        return self.status in {
            self.Status.RECEBIDO,
            self.Status.PREPARANDO,
            self.Status.ANALISANDO,
            self.Status.AGUARDANDO_CONFIRMACAO,
            self.Status.PROCESSANDO,
            self.Status.INTERROMPENDO,
        }

    @property
    def pode_excluir(self):
        # 'Excluir' no painel significa remover da fila/visão operacional.
        # Nunca exclui tabelas ou registros publicados das bases de dados.
        return not self.oculto_painel


class ItemLoteImportacao(models.Model):
    class Status(models.TextChoices):
        AGUARDANDO_FILA = 'AGUARDANDO_FILA', 'Aguardando na fila'
        PENDENTE = 'PENDENTE', 'Aguardando na fila'  # compatibilidade com lotes antigos
        PROCESSANDO = 'PROCESSANDO', 'Processando'
        PRONTO_IMPORTAR = 'PRONTO_IMPORTAR', 'Alteração detectada'
        CONCLUIDO = 'CONCLUIDO', 'Concluído'
        IGNORADO_DUPLICADO = 'IGNORADO_DUPLICADO', 'Ignorado — já importado'
        SEM_ALTERACAO = 'SEM_ALTERACAO', 'Sem alteração'
        REQUER_REVISAO = 'REQUER_REVISAO', 'Requer revisão'
        INTERROMPIDO = 'INTERROMPIDO', 'Interrompido'
        FALHOU = 'FALHOU', 'Falhou'

    lote = models.ForeignKey(LoteImportacao, on_delete=models.CASCADE, related_name='itens')
    caminho_relativo = models.CharField(max_length=700)
    nome_arquivo = models.CharField(max_length=255)
    uf = models.CharField(max_length=2, blank=True, db_index=True)
    dataset_slug = models.CharField(max_length=100, blank=True, db_index=True)
    dataset_label = models.CharField(max_length=255, blank=True)
    hash_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    fingerprint_conteudo = models.CharField(max_length=64, blank=True, db_index=True)
    progresso = models.PositiveSmallIntegerField(default=0)
    etapa = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=40, choices=Status.choices, default=Status.AGUARDANDO_FILA, db_index=True)
    motivo = models.TextField(blank=True)
    importacao = models.ForeignKey(
        'administracao.Importacao',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='itens_lote',
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['lote', 'caminho_relativo'], name='uniq_item_caminho_lote'),
        ]
        indexes = [
            models.Index(fields=['lote', 'status'], name='idx_lote_item_status'),
        ]
        verbose_name = 'item de lote de importação'
        verbose_name_plural = 'itens de lote de importação'

    def __str__(self):
        return f'Lote #{self.lote_id} - {self.caminho_relativo} - {self.get_status_display()}'
