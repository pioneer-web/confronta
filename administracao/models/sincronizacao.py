from django.conf import settings
from django.db import models


class FonteSincronizacao(models.Model):
    class Status(models.TextChoices):
        AGUARDANDO = 'AGUARDANDO', 'Aguardando na fila'
        VERIFICANDO = 'VERIFICANDO', 'Verificando fonte'
        BAIXANDO = 'BAIXANDO', 'Baixando'
        VALIDANDO = 'VALIDANDO', 'Validando'
        IMPORTANDO = 'IMPORTANDO', 'Importando'
        CONCLUIDO = 'CONCLUIDO', 'Atualizado'
        SEM_ALTERACAO = 'SEM_ALTERACAO', 'Sem alteração'
        FALHOU = 'FALHOU', 'Falhou'

    class Origem(models.TextChoices):
        AGENDADO = 'AGENDADO', 'Automático'
        MANUAL = 'MANUAL', 'Atualizar agora'

    fonte_slug = models.CharField(max_length=20, db_index=True)
    dataset_slug = models.CharField(max_length=100, db_index=True)
    uf = models.CharField(max_length=2, blank=True, db_index=True)
    origem = models.CharField(max_length=20, choices=Origem.choices, default=Origem.AGENDADO)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.AGUARDANDO, db_index=True)
    progresso = models.PositiveSmallIntegerField(default=0)
    etapa = models.CharField(max_length=180, blank=True)
    assinatura_remota = models.CharField(max_length=128, blank=True, db_index=True)
    bytes_baixados = models.BigIntegerField(default=0)
    registros_fonte = models.BigIntegerField(null=True, blank=True)
    novos = models.BigIntegerField(default=0)
    alterados = models.BigIntegerField(default=0)
    removidos = models.BigIntegerField(default=0)
    detalhes = models.JSONField(default=dict, blank=True)
    erro = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)
    ultima_atividade = models.DateTimeField(null=True, blank=True)
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='sincronizacoes_fontes',
    )
    importacao = models.ForeignKey(
        'administracao.Importacao',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='sincronizacoes_fontes',
    )

    class Meta:
        ordering = ['-criado_em']
        indexes = [
            models.Index(fields=['fonte_slug', 'dataset_slug', 'uf', '-criado_em'], name='idx_sync_fonte_dataset'),
            models.Index(fields=['status', 'criado_em'], name='idx_sync_status_data'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['fonte_slug', 'dataset_slug', 'uf'],
                condition=models.Q(status__in=['AGUARDANDO', 'VERIFICANDO', 'BAIXANDO', 'VALIDANDO', 'IMPORTANDO']),
                name='uq_sync_fonte_ativa',
            ),
        ]

    @property
    def ativo(self):
        return self.status in {
            self.Status.AGUARDANDO,
            self.Status.VERIFICANDO,
            self.Status.BAIXANDO,
            self.Status.VALIDANDO,
            self.Status.IMPORTANDO,
        }
