"""Perfis oficiais do SICOR / Crédito Rural.

Baseados no dicionário público atual do Banco Central. O importador tabular
valida os cabeçalhos reais antes de qualquer escrita e preserva campos extras
na RAW para tolerar evolução do leiaute oficial.
"""

from .base import DatasetSpec, F
from administracao.constants import FonteDados


def _t(name, *aliases, required=False):
    return F(name, *aliases, sql_type='text', required=required)


def _i(name, *aliases, required=False):
    return F(name, *aliases, sql_type='integer', required=required)


def _n(name, *aliases, required=False):
    return F(name, *aliases, sql_type='numeric', required=required)


def _d(name, *aliases, required=False):
    return F(name, *aliases, sql_type='date', required=required)


SICOR_DATASETS = (
    DatasetSpec(
        'sicor-operacao-basica', FonteDados.SICOR, 'sicor', 'Operações contratadas', 'Crédito Rural',
        'sicor_operacao_basica', 'raw_sicor_operacao_basica',
        ('sicor_operacao_basica_estado', 'operacao_basica_estado'), (),
        (
            _t('ref_bacen', 'REF_BACEN', required=True),
            _i('nu_ordem', 'NU_ORDEM', required=True),
            _t('cnpj_if', 'CNPJ_IF'),
            _d('dt_emissao', 'DT_EMISSAO'),
            _d('dt_vencimento', 'DT_VENCIMENTO'),
            _t('cd_inst_credito', 'CD_INST_CREDITO'),
            _t('cd_categ_emitente', 'CD_CATEG_EMITENTE'),
            _t('cd_fonte_recurso', 'CD_FONTE_RECURSO'),
            _t('cnpj_agente_invest', 'CNPJ_AGENTE_INVEST'),
            _t('cd_estado', 'CD_ESTADO'),
            _t('cd_ref_bacen_investimento', 'CD_REF_BACEN_INVESTIMENTO'),
            _t('cd_tipo_seguro', 'CD_TIPO_SEGURO'),
            _t('cd_empreendimento', 'CD_EMPREENDIMENTO'),
            _t('cd_programa', 'CD_PROGRAMA'),
            _t('cd_tipo_encarg_financ', 'CD_TIPO_ENCARG_FINANC'),
            _t('cd_tipo_irrigacao', 'CD_TIPO_IRRIGACAO'),
            _t('cd_tipo_agricultura', 'CD_TIPO_AGRICULTURA'),
            _t('cd_fase_ciclo_producao', 'CD_FASE_CICLO_PRODUCAO'),
            _t('cd_tipo_cultivo', 'CD_TIPO_CULTIVO'),
            _t('cd_tipo_intgr_consor', 'CD_TIPO_INTGR_CONSOR'),
            _t('cd_tipo_grao_semente', 'CD_TIPO_GRAO_SEMENTE'),
            _n('vl_aliq_proagro', 'VL_ALIQ_PROAGRO'),
            _n('vl_juros', 'VL_JUROS'),
            _n('vl_prestacao_investimento', 'VL_PRESTACAO_INVESTIMENTO'),
            _n('vl_prev_prod', 'VL_PREV_PROD'),
            _n('vl_quantidade', 'VL_QUANTIDADE'),
            _n('vl_receita_bruta_esperada', 'VL_RECEITA_BRUTA_ESPERADA'),
            _n('vl_parc_credito', 'VL_PARC_CREDITO'),
            _n('vl_rec_proprio', 'VL_REC_PROPRIO'),
            _n('vl_perc_risco_stn', 'VL_PERC_RISCO_STN'),
            _n('vl_perc_risco_fundo_const', 'VL_PERC_RISCO_FUNDO_CONST'),
            _n('vl_rec_proprio_srv', 'VL_REC_PROPRIO_SRV'),
            _n('vl_area_financ', 'VL_AREA_FINANC'),
            _t('cd_subprograma', 'CD_SUBPROGRAMA'),
            _n('vl_produtiv_obtida', 'VL_PRODUTIV_OBTIDA'),
            _d('dt_fim_colheita', 'DT_FIM_COLHEITA'),
            _d('dt_fim_plantio', 'DT_FIM_PLANTIO'),
            _d('dt_inic_colheita', 'DT_INIC_COLHEITA'),
            _d('dt_inic_plantio', 'DT_INIC_PLANTIO'),
            _n('vl_juros_enc_finan_posfix', 'VL_JUROS_ENC_FINAN_POSFIX'),
            _n('vl_perc_custo_efet_total', 'VL_PERC_CUSTO_EFET_TOTAL'),
            _t('cd_contrato_stn', 'CD_CONTRATO_STN'),
            _t('cd_cnpj_cadastrante', 'CD_CNPJ_CADASTRANTE'),
            _n('vl_area_informada', 'VL_AREA_INFORMADA'),
            _t('cd_ciclo_cultivar', 'CD_CICLO_CULTIVAR'),
            _t('cd_tipo_solo', 'CD_TIPO_SOLO'),
            _n('pc_bonus_car', 'PC_BONUS_CAR'),
        ),
        (
            ('REF_BACEN',), ('NU_ORDEM',), ('DT_EMISSAO', 'VL_PARC_CREDITO', 'CD_EMPREENDIMENTO'),
        ),
        ('dt_emissao', 'vl_parc_credito', 'cd_empreendimento', 'cd_estado'),
        mode='replace_year',
        filename_patterns=('sicor_operacao_basica_estado_', 'sicor_operacao_basica_'),
        data_kind='sicor_csv',
        year_partitioned=True,
    ),
    DatasetSpec(
        'sicor-complemento-operacao-basica', FonteDados.SICOR, 'sicor', 'Complemento da operação básica', 'Crédito Rural',
        'sicor_complemento_operacao_basica', 'raw_sicor_complemento_operacao_basica',
        ('sicor_complemento_operacao_basica', 'complemento_operacao_basica'), (),
        (
            _t('ref_bacen', 'REF_BACEN', required=True),
            _i('nu_ordem', 'NU_ORDEM', required=True),
            _t('ref_bacen_efetivo', 'REF_BACEN_EFETIVO'),
            _t('agencia_if', 'AGENCIA_IF'),
            _i('cd_ibge_municipio', 'CD_IBGE_MUNICIPIO'),
            _t('num_cedula_if', 'NUM_CEDULA_IF'),
        ),
        (
            ('REF_BACEN',), ('NU_ORDEM',), ('CD_IBGE_MUNICIPIO', 'REF_BACEN_EFETIVO', 'NUM_CEDULA_IF'),
        ),
        ('cd_ibge_municipio', 'ref_bacen_efetivo', 'agencia_if'),
        filename_patterns=('sicor_complemento_operacao_basica',),
        data_kind='sicor_csv',
    ),
    DatasetSpec(
        'sicor-glebas-contratadas', FonteDados.SICOR, 'sicor', 'Glebas contratadas — coordenadas geodésicas', 'Crédito Rural',
        'sicor_glebas_contratadas', 'raw_sicor_glebas_contratadas_pontos',
        ('sicor_glebas_contrat', 'sicor_glebas'), ('polygon',),
        (
            _t('ref_bacen', '#REF_BACEN', 'REF_BACEN', required=True),
            _i('nu_ordem', 'NU_ORDEM', required=True),
            _i('nu_identificador', 'NU_IDENTIFICADOR', required=True),
            _i('nu_indice_gleba', 'NU_INDICE_GLEBA', required=True),
            _i('nu_indice_ponto', 'NU_INDICE_PONTO', required=True),
            _n('vl_latitude', 'VL_LATITUDE', required=True),
            _n('vl_longitude', 'VL_LONGITUDE', required=True),
            _n('cgl_vl_altitude', 'CGL_VL_ALTITUDE'),
            _t('id_ponto', 'ID_PONTO', required=True),
        ),
        (
            ('REF_BACEN', '#REF_BACEN'), ('NU_ORDEM',), ('NU_IDENTIFICADOR',),
            ('NU_INDICE_GLEBA',), ('NU_INDICE_PONTO',), ('VL_LATITUDE',), ('VL_LONGITUDE',),
        ),
        ('nu_identificador', 'nu_indice_gleba', 'nu_indice_ponto', 'vl_latitude', 'vl_longitude'),
        filename_patterns=('sicor_glebas_contrat',),
        data_kind='sicor_gleba_points',
        geometry_srid=4674,
    ),
    DatasetSpec(
        'sicor-glebas-wkt', FonteDados.SICOR, 'sicor', 'Glebas financiadas — WKT', 'Crédito Rural',
        'sicor_glebas_wkt', 'raw_sicor_glebas_wkt',
        ('sicor_glebas_wkt', 'glebas_wkt'), ('polygon',),
        (
            _t('ref_bacen', 'REF_BACEN', required=True),
            _i('nu_ordem', 'NU_ORDEM', required=True),
            _i('nu_indice', 'NU_INDICE', required=True),
            _t('gt_geometria', 'GT_GEOMETRIA', required=True),
        ),
        (('REF_BACEN',), ('NU_ORDEM',), ('NU_INDICE',), ('GT_GEOMETRIA',)),
        ('gt_geometria', 'nu_indice'),
        mode='replace_year',
        filename_patterns=('sicor_glebas_wkt_',),
        data_kind='sicor_wkt',
        year_partitioned=True,
        geometry_wkt_field='gt_geometria',
        geometry_srid=4674,
    ),
    DatasetSpec(
        'sicor-propriedades', FonteDados.SICOR, 'sicor', 'Propriedades rurais', 'Crédito Rural',
        'sicor_propriedades', 'raw_sicor_propriedades',
        ('sicor_propriedades', 'propriedades'), (),
        (
            _t('ref_bacen', 'REF_BACEN', required=True),
            _i('nu_ordem', 'NU_ORDEM', required=True),
            _t('cd_cnpj_cpf', 'CD_CNPJ_CPF'),
            _t('cd_sncr', 'CD_SNCR'),
            _t('cd_nirf', 'CD_NIRF'),
            _t('cd_car', 'CD_CAR'),
        ),
        (('REF_BACEN',), ('NU_ORDEM',), ('CD_CAR', 'CD_SNCR')),
        ('cd_car', 'cd_sncr', 'cd_nirf'),
        filename_patterns=('sicor_propriedades',),
        data_kind='sicor_csv',
    ),
    DatasetSpec(
        'sicor-mutuarios', FonteDados.SICOR, 'sicor', 'Mutuários / beneficiários', 'Crédito Rural',
        'sicor_mutuarios', 'raw_sicor_mutuarios',
        ('sicor_mutuarios', 'mutuarios'), (),
        (
            _t('ref_bacen', 'REF_BACEN', required=True),
            _t('cd_cpf_cnpj', 'CD_CPF_CNPJ', required=True),
            _i('cd_sexo', 'CD_SEXO'),
            _i('cd_tipo_beneficiario', 'CD_TIPO_BENEFICIARIO'),
            _t('cd_dap', 'CD_DAP'),
            _t('cd_primeiro', 'CD_PRIMEIRO'),
        ),
        (('REF_BACEN',), ('CD_CPF_CNPJ',), ('CD_TIPO_BENEFICIARIO', 'CD_DAP', 'CD_PRIMEIRO')),
        ('cd_cpf_cnpj', 'cd_tipo_beneficiario', 'cd_dap'),
        filename_patterns=('sicor_mutuarios',),
        data_kind='sicor_csv',
    ),
)
