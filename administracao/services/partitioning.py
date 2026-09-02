from django.db import connection
from psycopg import sql

from administracao.constants import FONTE_SCHEMAS
from .field_matching import find_matching_field
from .normalization import table_columns

UF_CODES = {
    'AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG','PA',
    'PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO',
}

UF_NAMES = {
    'AC':'Acre','AL':'Alagoas','AP':'Amapá','AM':'Amazonas','BA':'Bahia','CE':'Ceará',
    'DF':'Distrito Federal','ES':'Espírito Santo','GO':'Goiás','MA':'Maranhão','MT':'Mato Grosso',
    'MS':'Mato Grosso do Sul','MG':'Minas Gerais','PA':'Pará','PB':'Paraíba','PR':'Paraná',
    'PE':'Pernambuco','PI':'Piauí','RJ':'Rio de Janeiro','RN':'Rio Grande do Norte',
    'RS':'Rio Grande do Sul','RO':'Rondônia','RR':'Roraima','SC':'Santa Catarina',
    'SP':'São Paulo','SE':'Sergipe','TO':'Tocantins',
}


def normalize_uf(value):
    uf = str(value or '').strip().upper()
    return uf if uf in UF_CODES else ''


def raw_table_for_import(spec, context=None):
    """Mantém uma RAW única por dataset SICAR.

    A v0.3.7 não cria tabelas RAW por estado. No fluxo estadual em lote, a UF é
    validada no staging e usada somente na promoção da partição lógica da tabela
    operacional nacional. Importações manuais legadas sem UF continuam válidas.
    """
    return spec.raw_table


def detect_sicar_ufs_in_staging(spec, schema, table):
    """Confirma integralmente as UFs presentes em COD_IMOVEL no staging.

    A validação diferencia três grupos:
    - códigos reconhecidos de uma das 27 UFs;
    - códigos no formato XX-... cujo prefixo não é uma UF brasileira;
    - códigos não vazios fora do padrão esperado.

    Isso impede que um lote estadual com registros de prefixo desconhecido seja
    promovido apenas porque a amostra principal apontou para uma UF válida.
    """
    if spec.fonte_slug != 'sicar':
        return {'aplicavel': False}

    columns = table_columns(schema, table)
    cod_field = next((field for field in spec.fields if field.canonical == 'cod_imovel'), None)
    if not cod_field:
        return {'aplicavel': True, 'detectadas': [], 'registros_reconheciveis': 0}

    column = find_matching_field(columns, cod_field.aliases)
    if not column:
        return {
            'aplicavel': True,
            'detectadas': [],
            'registros_reconheciveis': 0,
            'motivo': 'cod_imovel não localizado na RAW',
        }

    table_ident = sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))
    col_ident = sql.Identifier(column)
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT COUNT(*) AS total, "
                "COUNT(*) FILTER (WHERE {c} IS NULL OR trim({c}::text) = '') AS sem_codigo "
                "FROM {t}"
            ).format(c=col_ident, t=table_ident)
        )
        total_registros, sem_codigo = cursor.fetchone()
        cursor.execute(
            sql.SQL(
                "SELECT upper(left(trim({c}::text), 2)) AS prefixo, "
                "COUNT(*) AS total, "
                "COUNT(*) FILTER (WHERE trim({c}::text) ~* '^[A-Z]{{2}}-') AS formato_ok "
                "FROM {t} "
                "WHERE {c} IS NOT NULL AND trim({c}::text) <> '' "
                "GROUP BY 1 ORDER BY total DESC, prefixo"
            ).format(c=col_ident, t=table_ident)
        )
        rows = cursor.fetchall()

    distribuicao = []
    prefixos_nao_reconhecidos = []
    total_codigos = 0
    reconheciveis = 0
    sem_codigo = int(sem_codigo or 0)
    total_registros = int(total_registros or 0)
    fora_padrao = sem_codigo

    for prefixo, total, formato_ok in rows:
        prefixo = str(prefixo or '').upper()
        total = int(total or 0)
        formato_ok = int(formato_ok or 0)
        total_codigos += total
        fora_padrao += max(0, total - formato_ok)
        if formato_ok and prefixo in UF_CODES:
            distribuicao.append({'uf': prefixo, 'registros': formato_ok})
            reconheciveis += formato_ok
        elif formato_ok:
            prefixos_nao_reconhecidos.append({'prefixo': prefixo, 'registros': formato_ok})
            fora_padrao += formato_ok

    distribuicao.sort(key=lambda item: (-item['registros'], item['uf']))
    prefixos_nao_reconhecidos.sort(key=lambda item: (-item['registros'], item['prefixo']))
    return {
        'aplicavel': True,
        'campo': column,
        'detectadas': sorted(item['uf'] for item in distribuicao),
        'distribuicao': distribuicao,
        'prefixos_nao_reconhecidos': prefixos_nao_reconhecidos,
        'registros_reconheciveis': reconheciveis,
        'registros_total': total_registros,
        'registros_com_codigo': total_codigos,
        'registros_sem_codigo': sem_codigo,
        'registros_fora_padrao': fora_padrao,
        'bloqueia_importacao': bool(fora_padrao),
    }


# Compatibilidade interna com chamadas antigas. A validação da UF selecionada
# ocorre no pipeline depois deste relatório integral do staging.
def validate_sicar_uf_in_staging(spec, context, schema, table):
    return detect_sicar_ufs_in_staging(spec, schema, table)


def sicar_partition_has_rows(spec, uf):
    """Confirma que a partição lógica estadual ainda existe na tabela operacional.

    O fingerprint representa a última fonte confirmada, mas não deve esconder uma
    perda acidental da tabela/linhas operacionais. O teste é propositalmente barato
    (EXISTS/LIMIT 1) e só é usado antes de pular uma reimportação SICAR.
    """
    uf = normalize_uf(uf)
    if not uf or spec.fonte_slug != 'sicar':
        return False
    schema = FONTE_SCHEMAS[spec.fonte]
    with connection.cursor() as cursor:
        cursor.execute('SELECT to_regclass(%s)', [f'{schema}.{spec.stable_table}'])
        if cursor.fetchone()[0] is None:
            return False
        table_ident = sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(spec.stable_table))
        cursor.execute(
            sql.SQL(
                "SELECT 1 FROM {table} "
                "WHERE upper(left(trim(cod_imovel::text), 2))=%s "
                "AND trim(cod_imovel::text) ~* '^[A-Z]{{2}}-' LIMIT 1"
            ).format(table=table_ident),
            [uf],
        )
        return cursor.fetchone() is not None
