from django.db import models


class UnboundedNumericField(models.Field):
    """PostgreSQL NUMERIC sem precisão/escala impostas pelo modelo."""

    description = 'PostgreSQL unbounded NUMERIC'

    def db_type(self, connection):
        return 'numeric'

    def get_internal_type(self):
        return 'DecimalField'


class SicorOperacao(models.Model):
    """Tabela operacional anual de operações básicas SICOR."""

    id = models.BigAutoField(primary_key=True, db_column='_id')
    numero_linha = models.BigIntegerField(db_column='_numero_linha')
    ano_arquivo = models.IntegerField(db_column='_ano_arquivo', null=True, blank=True)

    ref_bacen = models.TextField(null=True, blank=True)
    nu_ordem = models.BigIntegerField(null=True, blank=True)
    cnpj_if = models.TextField(null=True, blank=True)
    dt_emissao = models.DateField(null=True, blank=True)
    dt_vencimento = models.DateField(null=True, blank=True)
    cd_inst_credito = models.TextField(null=True, blank=True)
    cd_categ_emitente = models.TextField(null=True, blank=True)
    cd_fonte_recurso = models.TextField(null=True, blank=True)
    cnpj_agente_invest = models.TextField(null=True, blank=True)
    cd_estado = models.TextField(null=True, blank=True)
    cd_ref_bacen_investimento = models.TextField(null=True, blank=True)
    cd_tipo_seguro = models.TextField(null=True, blank=True)
    cd_empreendimento = models.TextField(null=True, blank=True)
    cd_programa = models.TextField(null=True, blank=True)
    cd_tipo_encarg_financ = models.TextField(null=True, blank=True)
    cd_tipo_irrigacao = models.TextField(null=True, blank=True)
    cd_tipo_agricultura = models.TextField(null=True, blank=True)
    cd_fase_ciclo_producao = models.TextField(null=True, blank=True)
    cd_tipo_cultivo = models.TextField(null=True, blank=True)
    cd_tipo_intgr_consor = models.TextField(null=True, blank=True)
    cd_tipo_grao_semente = models.TextField(null=True, blank=True)
    vl_aliq_proagro = UnboundedNumericField(null=True, blank=True)
    vl_juros = UnboundedNumericField(null=True, blank=True)
    vl_prestacao_investimento = UnboundedNumericField(null=True, blank=True)
    vl_prev_prod = UnboundedNumericField(null=True, blank=True)
    vl_quantidade = UnboundedNumericField(null=True, blank=True)
    vl_receita_bruta_esperada = UnboundedNumericField(null=True, blank=True)
    vl_parc_credito = UnboundedNumericField(null=True, blank=True)
    vl_rec_proprio = UnboundedNumericField(null=True, blank=True)
    vl_perc_risco_stn = UnboundedNumericField(null=True, blank=True)
    vl_perc_risco_fundo_const = UnboundedNumericField(null=True, blank=True)
    vl_rec_proprio_srv = UnboundedNumericField(null=True, blank=True)
    vl_area_financ = UnboundedNumericField(null=True, blank=True)
    cd_subprograma = models.TextField(null=True, blank=True)
    vl_produtiv_obtida = UnboundedNumericField(null=True, blank=True)
    dt_fim_colheita = models.DateField(null=True, blank=True)
    dt_fim_plantio = models.DateField(null=True, blank=True)
    dt_inic_colheita = models.DateField(null=True, blank=True)
    dt_inic_plantio = models.DateField(null=True, blank=True)
    vl_juros_enc_finan_posfix = UnboundedNumericField(null=True, blank=True)
    vl_perc_custo_efet_total = UnboundedNumericField(null=True, blank=True)
    cd_contrato_stn = models.TextField(null=True, blank=True)
    cd_cnpj_cadastrante = models.TextField(null=True, blank=True)
    vl_area_informada = UnboundedNumericField(null=True, blank=True)
    cd_ciclo_cultivar = models.TextField(null=True, blank=True)
    cd_tipo_solo = models.TextField(null=True, blank=True)
    pc_bonus_car = UnboundedNumericField(null=True, blank=True)

    class Meta:
        db_table = '"dados_sicor"."sicor_operacoes"'
        indexes = [
            models.Index(fields=['ref_bacen'], name='sicor_operacoes_ref_idx'),
            models.Index(
                fields=['ref_bacen', 'nu_ordem'],
                name='sicor_operacoes_ref_ord_idx',
            ),
            models.Index(fields=['ano_arquivo'], name='sicor_operacoes_ano_idx'),
        ]
        verbose_name = 'operação SICOR'
        verbose_name_plural = 'operações SICOR'

    def __str__(self):
        return f'{self.ref_bacen or "(sem REF_BACEN)"}/{self.nu_ordem or "(sem NU_ORDEM)"}'
