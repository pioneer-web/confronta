from .base import DatasetSpec, F
from administracao.constants import FonteDados


# Perfil operacional confirmado a partir do pacote oficial tis_poligonais.zip
# analisado em 2026-08-29. A RAW continua preservando todos os campos recebidos;
# a tabela operacional mantém somente os atributos com nomes efetivamente
# observados no arquivo, sem inventar significado para campos não documentados.
FUNAI_DATASETS = (
    DatasetSpec(
        'funai-terras-indigenas', FonteDados.FUNAI, 'funai',
        'Terras Indígenas', 'FUNAI',
        'funai_terras_indigenas', 'raw_funai_terras_indigenas',
        ('tis_poligonais', 'terra_indigena', 'terras_indigenas', 'funai'), ('polygon',),
        (
            F('gid_origem', 'gid', sql_type='integer'),
            F('terrai_cod', 'terrai_cod', required=True, sql_type='integer'),
            F('terrai_nom', 'terrai_nom', required=True),
            F('etnia_nome', 'etnia_nome'),
            F('municipio', 'municipio_'),
            F('uf_sigla', 'uf_sigla'),
            F('superficie', 'superficie', sql_type='numeric'),
            F('fase_ti', 'fase_ti'),
            F('modalidade', 'modalidade'),
            F('reestudo_t', 'reestudo_t'),
            F('coordenacao_regional', 'cr'),
            F('faixa_fronteira', 'faixa_fron'),
            # Código administrativo é identificador, não quantidade. Mantemos texto
            # para não sofrer overflow nem perder zeros em versões futuras.
            F('undadm_cod', 'undadm_cod'),
            F('undadm_nom', 'undadm_nom'),
            F('undadm_sig', 'undadm_sig'),
            F('dominio_un', 'dominio_un'),
            F('data_atualizacao', 'data_atual', sql_type='date_flexible'),
            F('epsg_declarado', 'epsg', sql_type='integer'),
        ),
        (('terrai_cod',), ('terrai_nom',)),
        ('fase_ti', 'modalidade', 'etnia_nome', 'uf_sigla', 'superficie', 'data_atual'),
        mode='replace_table',
        filename_patterns=('tis_poligonais*.zip', 'tis_poligonais*.shp'),
        data_kind='spatial',
    ),
)
