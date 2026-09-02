from .base import DatasetSpec, F
from administracao.constants import FonteDados

# Perfil calibrado com o arquivo oficial real utilizado no projeto:
# adm_embargo_ibama_a.shp.zip / camada adm_embargo_ibama_a
# CRS observado no arquivo de referência: SIRGAS 2000 / EPSG:4674.
#
# v0.3.9: preservamos somente os atributos administrativos/territoriais
# necessários para a análise. Dados pessoais do autuado não são promovidos
# para a tabela operacional do CONFRONTA porque o produto não precisa deles
# para responder à interseção espacial.
IBAMA_DATASETS = (
    DatasetSpec(
        'ibama-termos-embargo',
        FonteDados.IBAMA,
        'ibama',
        'Termos de Embargo',
        'IBAMA',
        'ibama_embargo',
        'raw_ibama_embargo',
        ('ibama', 'embargo_ibama', 'adm_embargo_ibama', 'termo_embargo', 'termos_embargo'),
        ('polygon',),
        (
            F('embargo_id', 'objectid', 'embargo_id', 'id_embargo', 'seq_tad'),
            F('seq_tad', 'seq_tad', sql_type='integer'),
            F(
                'numero_embargo',
                'num_tad', 'numero_embargo', 'num_embargo', 'n_embargo',
                'numero', 'num_termo',
                required=True,
            ),
            F('serie_embargo', 'serie_tad', 'serie_embargo'),
            F('auto_infracao', 'num_auto_i', 'num_auto_infracao', 'auto_infracao'),
            F('serie_auto', 'serie_auto'),
            F('processo', 'num_proces', 'processo', 'num_processo'),
            F('data_embargo', 'dat_embarg', 'data_embargo', 'dt_embargo', 'data', sql_type='date'),
            F('situacao', 'situacao', 'status'),
            F('tipo_area', 'tipo_area'),
            F('bioma', 'des_tipo_b', 'bioma', 'descricao_bioma'),
            F('municipio', 'municipio', 'nome_municipio'),
            F('uf', 'uf', 'estado'),
            F('nome_imovel', 'nome_imove', 'nome_imovel'),
            F('unidade_ibama', 'unid_contr', 'unidade_ibama', 'unidade_controle'),
            F('descricao_infracao', 'des_infrac', 'descricao_infracao'),
            F('descricao_termo', 'des_tad', 'descricao_termo'),
            F('area_desmatada_informada_ha', 'qtd_area_d', 'area_desmatada', sql_type='numeric'),
            F('area_embargo_informada_ha', 'qtd_area_e', 'area_embargo', 'area_ha', sql_type='numeric'),
            F('data_ultima_alteracao', 'dat_ult_al', 'dat_ult_00', 'data_ultima_alteracao', sql_type='date'),
            F('data_base', 'data_base', 'dt_base', 'data_atualizacao', sql_type='date'),
        ),
        (('num_tad', 'numero_embargo', 'num_embargo', 'n_embargo', 'num_termo', 'numero'),),
        (
            'seq_tad', 'serie_tad', 'dat_embarg', 'origem_geo', 'num_auto_i',
            'num_proces', 'des_tad', 'des_infrac', 'qtd_area_e', 'municipio', 'uf',
        ),
        filename_patterns=('adm_embargo_ibama_a*.zip', 'adm_embargo_ibama*.zip'),
    ),
)
