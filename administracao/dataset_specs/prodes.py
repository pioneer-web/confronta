from .base import DatasetSpec, F
from administracao.constants import FonteDados

# A tabela operacional PRODES é consolidada. Os atributos abaixo são opcionais
# porque versões/biomas diferentes da fonte podem publicar schemas distintos.
# Quando presentes, eles são preservados para relatório e filtros do Módulo 2.
PRODES_COMMON_FIELDS = (
    F('fid_origem', 'fid', 'fid_origem', 'objectid', sql_type='integer'),
    F('uuid', 'uuid', 'id_uuid'),
    F('state', 'state', 'uf', 'estado'),
    F('path_row', 'path_row', 'pathrow'),
    F('main_class', 'main_class', 'classe_principal', 'classe'),
    F('class_name', 'class_name', 'nome_classe'),
    F('def_cloud', 'def_cloud', sql_type='numeric'),
    F('julian_day', 'julian_day', 'dia_juliano', sql_type='integer'),
    F('image_date', 'image_date', 'data_imagem', sql_type='date'),
    F('year', 'year', 'ano', 'year_prodes', sql_type='integer', required=True),
    F('area_km', 'area_km', 'area_km2', sql_type='numeric'),
    F('scene_id', 'scene_id', 'cena'),
    F('source', 'source', 'fonte_dado'),
    F('satellite', 'satellite', 'satelite'),
    F('sensor', 'sensor'),
)


def _spec(slug, label, grupo, tokens, bioma, tipo, filename_patterns=()):
    return DatasetSpec(
        slug,
        FonteDados.PRODES,
        'prodes',
        label,
        grupo,
        'prodes_ocorrencia',
        f'raw_{slug.replace("-", "_")}',
        tokens,
        ('polygon',),
        PRODES_COMMON_FIELDS,
        (('year', 'ano', 'year_prodes'),),
        ('classe', 'class', 'prodes', 'main_class', 'uuid', 'satellite'),
        (('bioma', bioma), ('tipo_prodes', tipo)),
        'replace_partition',
        tuple(filename_patterns),
    )


PRODES_DATASETS = (
    _spec(
        'prodes-amazonia-desmatamento',
        'Amazônia — Incremento anual no desmatamento',
        'Amazônia',
        ('amazonia', 'amazon', 'desmatamento', 'prodes'),
        'AMAZONIA',
        'DESMATAMENTO',
        ('yearly_deforestation_amazonia_legal',),
    ),
    _spec(
        'prodes-amazonia-nao-florestal',
        'Amazônia — Supressão da vegetação nativa não florestal',
        'Amazônia',
        ('amazonia', 'amazon', 'nao_florestal', 'non_forest', 'supressao', 'prodes'),
        'AMAZONIA',
        'SUPRESSAO_NAO_FLORESTAL',
        ('yearly_deforestation_nf_biome_amazonia', 'yearly_deforestation_non_forest_biome_amazonia'),
    ),
    _spec(
        'prodes-cerrado-supressao',
        'Cerrado — Incremento anual na supressão',
        'Cerrado',
        ('cerrado', 'supressao', 'prodes'),
        'CERRADO',
        'SUPRESSAO_VEGETACAO_NATIVA',
        ('yearly_deforestation_biome_cerrado',),
    ),
    _spec(
        'prodes-mata-atlantica-supressao',
        'Mata Atlântica — Incremento anual na supressão',
        'Mata Atlântica',
        ('mata_atlantica', 'mataatlantica', 'supressao', 'prodes'),
        'MATA_ATLANTICA',
        'SUPRESSAO_VEGETACAO_NATIVA',
        ('yearly_deforestation_biome_mata_atlantica',),
    ),
    _spec(
        'prodes-caatinga-supressao',
        'Caatinga — Incremento anual na supressão',
        'Caatinga',
        ('caatinga', 'supressao', 'prodes'),
        'CAATINGA',
        'SUPRESSAO_VEGETACAO_NATIVA',
        ('yearly_deforestation_biome_caatinga',),
    ),
    _spec(
        'prodes-pampa-supressao',
        'Pampa — Incremento anual na supressão',
        'Pampa',
        ('pampa', 'supressao', 'prodes'),
        'PAMPA',
        'SUPRESSAO_VEGETACAO_NATIVA',
        ('yearly_deforestation_biome_pampa',),
    ),
    _spec(
        'prodes-pantanal-supressao',
        'Pantanal — Incremento anual na supressão',
        'Pantanal',
        ('pantanal', 'supressao', 'prodes'),
        'PANTANAL',
        'SUPRESSAO_VEGETACAO_NATIVA',
        ('yearly_deforestation_biome_pantanal',),
    ),
)
