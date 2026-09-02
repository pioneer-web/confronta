from django.db import models


class SicarEstado(models.Model):
    class Status(models.TextChoices):
        NUNCA_IMPORTADO = 'NUNCA_IMPORTADO', 'Nunca importado'
        EM_FILA = 'EM_FILA', 'Em fila'
        PROCESSANDO = 'PROCESSANDO', 'Processando'
        ATUALIZADO = 'ATUALIZADO', 'Atualizado'
        SEM_ALTERACAO = 'SEM_ALTERACAO', 'Sem alteração'
        ATENCAO = 'ATENCAO', 'Atenção'
        FALHOU = 'FALHOU', 'Falhou'

    uf = models.CharField(max_length=2, unique=True, db_index=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.NUNCA_IMPORTADO, db_index=True)
    ultima_verificacao = models.DateTimeField(null=True, blank=True)
    ultima_atualizacao = models.DateTimeField(null=True, blank=True)
    ultimo_lote = models.ForeignKey(
        'administracao.LoteImportacao', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='estados_sicar',
    )
    detalhes = models.JSONField(default=dict, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['uf']
        verbose_name = 'estado SICAR'
        verbose_name_plural = 'estados SICAR'

    def __str__(self):
        return f'SICAR {self.uf} — {self.get_status_display()}'


class SicarFingerprintCamada(models.Model):
    uf = models.CharField(max_length=2, db_index=True)
    dataset_slug = models.CharField(max_length=100, db_index=True)
    hash_conteudo = models.CharField(max_length=64, db_index=True)
    hash_arquivo = models.CharField(max_length=64, blank=True, db_index=True)
    ultima_verificacao = models.DateTimeField()
    ultima_atualizacao = models.DateTimeField(null=True, blank=True)
    ultima_importacao = models.ForeignKey(
        'administracao.Importacao', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='fingerprints_sicar',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['uf', 'dataset_slug'], name='uniq_sicar_fingerprint_uf_dataset'),
        ]
        indexes = [
            models.Index(fields=['uf', 'dataset_slug'], name='idx_sicar_fp_uf_dataset'),
        ]
        ordering = ['uf', 'dataset_slug']
        verbose_name = 'fingerprint de camada SICAR'
        verbose_name_plural = 'fingerprints de camadas SICAR'

    def __str__(self):
        return f'{self.uf}/{self.dataset_slug}'

class SicarColetaAutomatica(models.Model):
    class Origem(models.TextChoices):
        MANUAL = 'MANUAL', 'Manual'
        AGENDADA = 'AGENDADA', 'Agendada'

    class Status(models.TextChoices):
        AGUARDANDO_FILA = 'AGUARDANDO_FILA', 'Aguardando na fila'
        BAIXANDO = 'BAIXANDO', 'Baixando do SICAR'
        AGUARDANDO_IMPORTACAO = 'AGUARDANDO_IMPORTACAO', 'Aguardando importação'
        IMPORTANDO = 'IMPORTANDO', 'Importando'
        SEM_ALTERACAO = 'SEM_ALTERACAO', 'Sem alteração'
        CONCLUIDO = 'CONCLUIDO', 'Concluído'
        ATENCAO = 'ATENCAO', 'Concluído com pendências'
        FALHOU = 'FALHOU', 'Falhou'

    uf = models.CharField(max_length=2, default='PE', db_index=True)
    dataset_slug = models.CharField(max_length=100, default='sicar-perimetros', db_index=True)
    origem = models.CharField(max_length=20, choices=Origem.choices, default=Origem.MANUAL, db_index=True)
    status = models.CharField(
        max_length=40, choices=Status.choices,
        default=Status.AGUARDANDO_FILA, db_index=True,
    )
    solicitado_por = models.ForeignKey(
        'administracao.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='coletas_sicar_solicitadas',
    )
    lote = models.ForeignKey(
        'administracao.LoteImportacao', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='coletas_sicar_automaticas',
    )
    data_agendada = models.DateField(null=True, blank=True, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)
    detalhes = models.JSONField(default=dict, blank=True)
    erro = models.TextField(blank=True)
    progresso_percentual = models.PositiveSmallIntegerField(default=0)
    etapa = models.CharField(max_length=180, blank=True)
    bytes_baixados = models.BigIntegerField(default=0)
    ultima_atividade = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-criado_em']
        indexes = [
            models.Index(fields=['status', 'criado_em'], name='idx_sicar_coleta_fila'),
            models.Index(fields=['uf', '-criado_em'], name='idx_sicar_coleta_uf_data'),
        ]
        verbose_name = 'coleta automática SICAR'
        verbose_name_plural = 'coletas automáticas SICAR'

    def __str__(self):
        return f'SICAR {self.uf} — {self.get_status_display()}'

