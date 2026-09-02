from .base import DatasetSpec, F
from administracao.constants import FonteDados

INCRA_DATASETS = (
    DatasetSpec(
        'incra-assentamentos', FonteDados.INCRA, 'incra', 'Projetos de Assentamento Total', 'INCRA',
        'incra_assentamentos', 'raw_incra_assentamentos',
        ('assentamento', 'assentamentos', 'projeto_assentamento'), ('polygon',),
        (
            F('codigo', 'codigo', 'cod_projeto', 'cd_sipra', 'id'),
            F('nome', 'nome', 'nome_projeto', 'nm_projeto', 'nome_proje', required=True),
            F('modalidade', 'modalidade', 'tipo', 'descricao_tipo'),
            F('situacao', 'situacao', 'status'),
            F('fase', 'fase'),
            F('municipio', 'municipio', 'nome_municipio', 'nm_municip'),
            F('uf', 'uf', 'estado', 'cd_uf'),
            F('area_ha', 'area_hecta', 'area_ha', 'area', sql_type='numeric'),
            F('area_calculada_ha', 'area_calc_', 'area_calculada', sql_type='numeric'),
            F('capacidade_familias', 'capacidade', sql_type='integer'),
            F('quantidade_familias', 'num_famili', 'numero_familias', sql_type='integer'),
            F('data_criacao', 'data_de_cr', 'data_criacao', sql_type='date_flexible'),
            F('forma_obtencao', 'forma_obte', 'forma_obtencao'),
            F('data_obtencao', 'data_obten', 'data_obtencao', sql_type='date_flexible'),
            F('descricao', 'descricao_', 'descricao', 'observacao'),
        ),
        (('nome', 'nome_projeto', 'nm_projeto', 'nome_proje'),),
        ('modalidade', 'situacao', 'municipio', 'cd_sipra', 'capacidade', 'num_famili', 'fase'),
        filename_patterns=('assentamento*.zip', 'assentamentos*.zip', 'projeto_assentamento*.zip'),
    ),
    DatasetSpec(
        'incra-quilombolas', FonteDados.INCRA, 'incra', 'Áreas Quilombolas', 'INCRA',
        'incra_areas_quilombolas', 'raw_incra_areas_quilombolas',
        ('quilombola', 'quilombolas', 'territorio_quilombola'), ('polygon',),
        (
            F('identificacao', 'identificacao', 'id', 'codigo', 'cd_quilomb', 'cd_sr'),
            F('codigo_quilombola', 'cd_quilomb', 'codigo_quilombola'),
            F('codigo_sr', 'cd_sr', 'codigo_sr'),
            F('processo', 'nr_process', 'processo'),
            F('nome', 'nome', 'comunidade', 'territorio', 'nome_comunidade', 'nm_comunid', required=True),
            F('situacao', 'situacao', 'status', 'st_titulad'),
            F('fase', 'fase', 'etapa'),
            F('municipio', 'municipio', 'nome_municipio', 'nm_municip'),
            F('uf', 'uf', 'estado', 'cd_uf'),
            F('area_ha', 'nr_area_ha', 'area_ha', 'area', sql_type='numeric'),
            F('area_calculada_ha', 'area_calc_', 'area_calculada', sql_type='numeric'),
            F('quantidade_familias', 'nr_familia', 'numero_familias', sql_type='integer'),
            F('data_publicacao', 'dt_publica', 'data_publicacao', sql_type='date_flexible'),
            # Campo adicional presente na base oficial analisada em 2026-08.
            # Mantemos o nome de origem porque a semântica oficial ainda não foi
            # documentada com segurança; a RAW preserva o valor original.
            F('dt_public1', 'dt_public1', sql_type='date_flexible'),
            F('data_titulacao', 'dt_titulac', 'data_titulacao', sql_type='date_flexible'),
            F('data_decreto', 'dt_decreto', 'data_decreto', sql_type='date_flexible'),
            F('codigo_sipra', 'cd_sipra', 'codigo_sipra'),
            F('responsavel', 'responsave', 'responsavel'),
            F('esfera', 'esfera'),
            F('observacao', 'ob_descric', 'observacao', 'descricao'),
            # Atributos adicionais confirmados no Shapefile oficial. Mantemos
            # nomes canônicos conservadores quando a fonte não documenta o
            # significado completo do campo.
            F('nr_perimet', 'nr_perimet', sql_type='numeric'),
            F('tp_levanta', 'tp_levanta'),
            F('nr_escalao', 'nr_escalao'),
            F('perimetro_', 'perimetro_', sql_type='numeric'),
        ),
        (('nome', 'comunidade', 'territorio', 'nome_comunidade', 'nm_comunid'),),
        ('situacao', 'municipio', 'processo', 'nr_process', 'fase', 'cd_sr', 'cd_quilomb'),
        filename_patterns=('areas_de_quilombolas*.zip', 'areas_quilombolas*.zip', 'quilombolas*.zip'),
    ),
)
