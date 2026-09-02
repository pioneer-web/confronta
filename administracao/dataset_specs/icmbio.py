from .base import DatasetSpec, F
from administracao.constants import FonteDados


ICMBIO_DATASETS = (
    DatasetSpec(
        'icmbio-areas-embargadas', FonteDados.ICMBIO, 'icmbio', 'Áreas Embargadas', 'ICMBio',
        'icmbio_embargo', 'raw_icmbio_embargo',
        ('icmbio', 'embargos_icmbio', 'areas_embargadas', 'area_embargada'),
        ('polygon',),
        (
            F('embargo_id', 'embargo_id', 'id_embargo', 'id'),
            F('numero_embargo', 'numero_embargo', 'num_embargo', 'numero', 'num_termo', required=True),
            F('data_embargo', 'data_embargo', 'dt_embargo', 'data', sql_type='date'),
            F('situacao', 'situacao', 'status'),
            F('area_ha', 'area_ha', 'area', sql_type='numeric'),
            F('data_base', 'data_base', 'dt_base', 'data_atualizacao', sql_type='date'),
        ),
        (('numero_embargo', 'num_embargo', 'numero', 'num_termo'),),
        ('situacao', 'data', 'area'),
        filename_patterns=('embargos_icmbio*.zip', 'areas_embargadas_icmbio*.zip'),
    ),
    DatasetSpec(
        'icmbio-unidades-conservacao-federais', FonteDados.ICMBIO, 'icmbio',
        'Unidades de Conservação Federais', 'ICMBio',
        'icmbio_unidade_conservacao_federal', 'raw_icmbio_unidade_conservacao_federal',
        (
            'limite_ucs_federais', 'ucs_federais', 'unidades_conservacao_federais',
            'unidade_conservacao_federal',
        ),
        ('polygon',),
        (
            F('uc_id', 'id', 'uc_id'),
            F('nome_uc', 'nomeuc', 'nome_uc', 'nome', required=True),
            F('codigo_cnuc', 'cnuc', 'cd_cnuc', 'codigo_cnuc', required=True),
            F('ano_criacao', 'criacaoano', 'ano_criacao', 'ano', sql_type='year'),
            F('area_ha', 'areahaalb', 'area_ha', 'area', sql_type='numeric'),
            F('perimetro_m', 'perimm', 'perimetro_m', 'perimetro', sql_type='numeric'),
            F('ato_criacao', 'criacaoato', 'ato_criacao'),
            F('esfera', 'esferaadm', 'esfera', 'esfera_adm'),
            F('grupo_manejo', 'grupouc', 'grupo_manejo', 'grupo'),
            F('biomas', 'biomas'),
            F('gerencia_regional', 'gregional', 'gerencia_regional'),
            F('fuso', 'fusoabrang', 'fuso'),
            F('demarcacao', 'demarcacao'),
            F('escala', 'escalauc', 'escala'),
            F('bioma_predominante', 'bioma_pred', 'bioma_predominante'),
            F('categoria_iucn', 'cat_iucn', 'categoria_iucn'),
            F('uf', 'uf'),
            F('categoria_manejo', 'categoria_', 'categoria_manejo', 'categoria'),
            F('sigla_categoria', 'sigla_cate', 'sigla_categoria'),
            F('dominio', 'dominio'),
        ),
        (
            ('nomeuc', 'nome_uc', 'nome'),
            ('cnuc', 'cd_cnuc', 'codigo_cnuc'),
        ),
        ('esferaadm', 'grupouc', 'categoria_', 'biomas', 'gregional'),
        filename_patterns=('limite_ucs_federais*.zip', 'ucs_federais*.zip'),
    ),
)
