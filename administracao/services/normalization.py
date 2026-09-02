from django.db import connection
from psycopg import sql
from .field_matching import norm, find_matching_field


def geometry_family(value):
    v = norm(value)
    if 'polygon' in v:
        return 'polygon'
    if 'line' in v or 'curve' in v:
        return 'line'
    if 'point' in v:
        return 'point'
    return v


def table_columns(schema, table):
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position',
            [schema,table],
        )
        return [r[0] for r in cursor.fetchall()]


def find_alias(columns, aliases):
    return find_matching_field(columns, aliases)


def preferred_slash_date_order(dmy_evidence, mdy_evidence):
    """Escolhe uma ordem apenas quando uma convenção é claramente dominante.

    Valores inequívocos continuam sendo interpretados individualmente mesmo
    quando a coluna contém DD/MM e MM/DD misturados. A ordem dominante é usada
    somente para valores ambíguos, como 03/04/2024.
    """
    dmy = int(dmy_evidence or 0)
    mdy = int(mdy_evidence or 0)
    if dmy and not mdy:
        return 'DMY'
    if mdy and not dmy:
        return 'MDY'
    if not dmy and not mdy:
        return None
    major = max(dmy, mdy)
    minor = min(dmy, mdy)
    # Evita escolher uma convenção quando a fonte está realmente dividida.
    # Um outlier isolado (como ocorre no arquivo real do INCRA) não deve
    # bloquear centenas de datas válidas da mesma coluna.
    if major >= max(3, minor * 3):
        return 'DMY' if dmy > mdy else 'MDY'
    return None


def analyze_flexible_date_column(schema, table, column):
    """Mapeia formatos encontrados sem bloquear a promoção.

    Bases públicas podem conter, na mesma coluna, DD/MM, MM/DD, ISO e pequenos
    erros de digitação. O relatório preserva essa evidência; a conversão usa
    regras determinísticas e valores não seguros viram NULL, nunca datas
    inventadas.
    """
    if not column:
        return {
            'campo_origem': '', 'preferencia_ambiguos': None,
            'dmy_inequivocos': 0, 'mdy_inequivocos': 0,
            'ambiguos': 0, 'ymd': 0, 'dmy_compacto': 0,
            'marcadores_sem_data': 0, 'nao_reconhecidos': 0,
        }
    ident = sql.Identifier(column)
    table_ident = sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(r"""
                WITH vals AS (
                    SELECT regexp_replace(trim({c}::text), '[ T].*$', '') AS v
                    FROM {t}
                    WHERE {c} IS NOT NULL AND trim({c}::text) <> ''
                ), classified AS (
                    SELECT v,
                        CASE
                            WHEN v IN ('-', '--', '0000/00/00', '00/00/0000', '0000-00-00') THEN 'empty_marker'
                            WHEN v ~ '^\d{{4}}[-/.]\d{{1,2}}[-/.]\d{{1,2}}$' THEN 'ymd'
                            WHEN v ~ '^\d{{1,2}}[-/.]\d{{1,2}}[-/.]\d{{4}}$' THEN
                                CASE
                                    WHEN split_part(translate(v,'.-','//'),'/',1)::integer > 12
                                         AND split_part(translate(v,'.-','//'),'/',1)::integer <= 31
                                         AND split_part(translate(v,'.-','//'),'/',2)::integer BETWEEN 1 AND 12 THEN 'dmy'
                                    WHEN split_part(translate(v,'.-','//'),'/',2)::integer > 12
                                         AND split_part(translate(v,'.-','//'),'/',2)::integer <= 31
                                         AND split_part(translate(v,'.-','//'),'/',1)::integer BETWEEN 1 AND 12 THEN 'mdy'
                                    WHEN split_part(translate(v,'.-','//'),'/',1)::integer BETWEEN 1 AND 12
                                         AND split_part(translate(v,'.-','//'),'/',2)::integer BETWEEN 1 AND 12 THEN 'ambiguous'
                                    ELSE 'unrecognized'
                                END
                            WHEN v ~ '^\d{{1,2}}/\d{{6}}$' THEN 'compact_dmy'
                            ELSE 'unrecognized'
                        END AS kind
                    FROM vals
                )
                SELECT
                    count(*) FILTER (WHERE kind='dmy'),
                    count(*) FILTER (WHERE kind='mdy'),
                    count(*) FILTER (WHERE kind='ambiguous'),
                    count(*) FILTER (WHERE kind='ymd'),
                    count(*) FILTER (WHERE kind='compact_dmy'),
                    count(*) FILTER (WHERE kind='empty_marker'),
                    count(*) FILTER (WHERE kind='unrecognized')
                FROM classified
            """).format(c=ident, t=table_ident)
        )
        dmy, mdy, ambiguous, ymd, compact_dmy, empty_markers, unrecognized = cursor.fetchone()
    profile = {
        'campo_origem': column,
        'dmy_inequivocos': int(dmy or 0),
        'mdy_inequivocos': int(mdy or 0),
        'ambiguos': int(ambiguous or 0),
        'ymd': int(ymd or 0),
        'dmy_compacto': int(compact_dmy or 0),
        'marcadores_sem_data': int(empty_markers or 0),
        'nao_reconhecidos': int(unrecognized or 0),
    }
    profile['preferencia_ambiguos'] = preferred_slash_date_order(
        profile['dmy_inequivocos'], profile['mdy_inequivocos']
    )
    profile['formatos_mistos'] = bool(profile['dmy_inequivocos'] and profile['mdy_inequivocos'])
    return profile


def infer_slash_date_order(schema, table, column):
    # Compatibilidade interna: retorna somente a preferência para valores
    # ambíguos. Mistura de formatos não é mais motivo para abortar a base.
    return analyze_flexible_date_column(schema, table, column).get('preferencia_ambiguos')


def _safe_make_date(year_expr, month_expr, day_expr):
    """Cria DATE sem depender de DateStyle e sem lançar erro por data inválida.

    Todos os componentes chegam como expressões inteiras já extraídas de valores
    com formato reconhecido. Antes de chamar make_date(), validamos ano, mês e o
    último dia possível do mês (incluindo ano bissexto). Assim nenhum texto bruto
    é convertido diretamente para DATE pelo PostgreSQL.
    """
    max_day = sql.SQL(r"""
        CASE
            WHEN {m} IN (1,3,5,7,8,10,12) THEN 31
            WHEN {m} IN (4,6,9,11) THEN 30
            WHEN {m} = 2 THEN
                CASE
                    WHEN (MOD({y}, 400) = 0 OR (MOD({y}, 4) = 0 AND MOD({y}, 100) <> 0)) THEN 29
                    ELSE 28
                END
            ELSE 0
        END
    """).format(y=year_expr, m=month_expr)
    return sql.SQL(r"""
        CASE
            WHEN {y} BETWEEN 1000 AND 9999
             AND {m} BETWEEN 1 AND 12
             AND {d} BETWEEN 1 AND ({max_day})
            THEN make_date({y}, {m}, {d})
            ELSE NULL
        END
    """).format(y=year_expr, m=month_expr, d=day_expr, max_day=max_day)


def _flexible_date_expr(column, date_order=None):
    """Normaliza datas públicas por valor, sem depender do DateStyle do PostgreSQL.

    Regras conservadoras:
    - ISO YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD;
    - DD/MM e MM/DD inequívocos são interpretados individualmente;
    - valores ambíguos usam somente a convenção claramente dominante da coluna;
    - DD/MMYYYY (erro real observado no INCRA) é recuperado quando seguro;
    - marcadores, anos incompletos e datas impossíveis viram NULL na operacional;
    - o valor original continua preservado integralmente na RAW.
    """
    c = sql.Identifier(column)
    raw = sql.SQL('trim({}::text)').format(c)
    value = sql.SQL("regexp_replace({}, '[ T].*$', '')").format(raw)
    normalized = sql.SQL("translate({}, '.-', '//')").format(value)

    first = sql.SQL("split_part({}, '/', 1)::integer").format(normalized)
    second = sql.SQL("split_part({}, '/', 2)::integer").format(normalized)
    third = sql.SQL("split_part({}, '/', 3)::integer").format(normalized)

    # YYYY/MM/DD
    safe_ymd = _safe_make_date(first, second, third)
    # DD/MM/YYYY
    safe_dmy = _safe_make_date(third, second, first)
    # MM/DD/YYYY
    safe_mdy = _safe_make_date(third, first, second)

    compact_tail = sql.SQL("split_part({}, '/', 2)").format(value)
    compact_month = sql.SQL("left({}, 2)::integer").format(compact_tail)
    compact_year = sql.SQL("right({}, 4)::integer").format(compact_tail)
    compact_day = sql.SQL("split_part({}, '/', 1)::integer").format(value)
    safe_compact = _safe_make_date(compact_year, compact_month, compact_day)

    preferred = str(date_order or '').upper()
    ambiguous_expr = safe_mdy if preferred == 'MDY' else (
        safe_dmy if preferred == 'DMY' else sql.SQL('NULL')
    )

    return sql.SQL(r"""
        CASE
            WHEN {raw} IS NULL OR {raw} = '' THEN NULL
            WHEN {value} IN ('-', '--', '0000/00/00', '00/00/0000', '0000-00-00') THEN NULL
            WHEN {value} ~ '^\d{{4}}[-/.]\d{{1,2}}[-/.]\d{{1,2}}$'
                THEN {safe_ymd}
            WHEN {value} ~ '^\d{{1,2}}[-/.]\d{{1,2}}[-/.]\d{{4}}$' THEN
                CASE
                    WHEN {a} > 12 AND {a} <= 31 AND {b} BETWEEN 1 AND 12 THEN {safe_dmy}
                    WHEN {b} > 12 AND {b} <= 31 AND {a} BETWEEN 1 AND 12 THEN {safe_mdy}
                    WHEN {a} BETWEEN 1 AND 12 AND {b} BETWEEN 1 AND 12 THEN {ambiguous}
                    ELSE NULL
                END
            WHEN {value} ~ '^\d{{1,2}}/\d{{6}}$'
                THEN {safe_compact}
            ELSE NULL
        END
    """).format(
        raw=raw, value=value, a=first, b=second,
        safe_ymd=safe_ymd, safe_dmy=safe_dmy, safe_mdy=safe_mdy,
        ambiguous=ambiguous_expr, safe_compact=safe_compact,
    )


def _strict_date_expr(column):
    """Parser seguro para fontes já validadas como ISO ou DD/MM.

    Substitui casts/to_date permissivos por make_date com validação de calendário.
    Isso evita erros futuros de DateStyle e também evita que o PostgreSQL ajuste
    silenciosamente datas impossíveis.
    """
    c = sql.Identifier(column)
    raw = sql.SQL('trim({}::text)').format(c)
    value = sql.SQL("regexp_replace({}, '[ T].*$', '')").format(raw)
    normalized = sql.SQL("translate({}, '.-', '//')").format(value)
    first = sql.SQL("split_part({}, '/', 1)::integer").format(normalized)
    second = sql.SQL("split_part({}, '/', 2)::integer").format(normalized)
    third = sql.SQL("split_part({}, '/', 3)::integer").format(normalized)
    return sql.SQL(r"""
        CASE
            WHEN {raw} IS NULL OR {raw} = '' THEN NULL
            WHEN {value} IN ('-', '--', '0000/00/00', '00/00/0000', '0000-00-00') THEN NULL
            WHEN {value} ~ '^\d{{4}}[-/.]\d{{1,2}}[-/.]\d{{1,2}}$'
                THEN {safe_ymd}
            WHEN {value} ~ '^\d{{1,2}}[-/.]\d{{1,2}}[-/.]\d{{4}}$'
                THEN {safe_dmy}
            ELSE NULL
        END
    """).format(
        raw=raw, value=value,
        safe_ymd=_safe_make_date(first, second, third),
        safe_dmy=_safe_make_date(third, second, first),
    )


def cast_expr(column, sql_type, date_order=None):
    if column is None:
        return sql.SQL('NULL')
    c = sql.Identifier(column)
    if sql_type == 'numeric':
        return sql.SQL("CASE WHEN replace(trim({0}::text), ',', '.') ~ '^[-+]?[0-9]+([.][0-9]+)?$' THEN replace(trim({0}::text), ',', '.')::numeric ELSE NULL END").format(c)
    if sql_type == 'integer':
        return sql.SQL("CASE WHEN trim({0}::text) ~ '^[0-9]{{4}}$|^[-+]?[0-9]+$' THEN trim({0}::text)::integer ELSE NULL END").format(c)
    if sql_type == 'year':
        return sql.SQL("""
            CASE
                WHEN trim({0}::text) ~ '^\\d{{4}}$'
                    THEN trim({0}::text)::integer
                WHEN trim({0}::text) ~ '^\\d{{2}}[-/]\\d{{2}}[-/]\\d{{4}}$'
                    THEN right(trim({0}::text), 4)::integer
                WHEN trim({0}::text) ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'
                    THEN left(trim({0}::text), 4)::integer
                ELSE NULL
            END
        """).format(c)
    if sql_type == 'date_flexible':
        return _flexible_date_expr(column, date_order=date_order)
    if sql_type == 'date':
        return _strict_date_expr(column)
    return sql.SQL('{}::text').format(c)


def analyze_date_conversion(schema, table, column, sql_type, date_order=None):
    """Valida a conversão completa antes de qualquer escrita na operacional.

    Retorna quantidade e amostras dos valores que permanecerão apenas na RAW.
    A própria expressão é executada na pré-validação; portanto um erro de parser
    é detectado antes de TRUNCATE/DELETE/INSERT da tabela operacional.
    """
    if not column or sql_type not in {'date', 'date_flexible'}:
        return {'nao_convertidos': 0, 'amostras_nao_convertidas': []}
    expr = cast_expr(column, sql_type, date_order=date_order)
    raw = sql.SQL('trim({}::text)').format(sql.Identifier(column))
    source = sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))
    meaningful = sql.SQL(
        "{r} IS NOT NULL AND {r} <> '' AND {r} NOT IN ('-', '--', '0000/00/00', '00/00/0000', '0000-00-00')"
    ).format(r=raw)
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL('SELECT count(*) FROM {} WHERE {} AND ({}) IS NULL').format(
                source, meaningful, expr
            )
        )
        count = int(cursor.fetchone()[0] or 0)
        cursor.execute(
            sql.SQL('SELECT DISTINCT {} FROM {} WHERE {} AND ({}) IS NULL ORDER BY 1 LIMIT 20').format(
                raw, source, meaningful, expr
            )
        )
        samples = [row[0] for row in cursor.fetchall()]
    return {'nao_convertidos': count, 'amostras_nao_convertidas': samples}

def geometry_metadata(schema, table):
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT f_geometry_column,type,srid FROM geometry_columns WHERE f_table_schema=%s AND f_table_name=%s ORDER BY f_geometry_column LIMIT 1',
            [schema,table],
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {'column': row[0], 'type': row[1] or '', 'srid': row[2]}


def geometry_column(schema, table):
    meta = geometry_metadata(schema, table)
    return meta['column'] if meta else None


def _promoted_geometry_type(source_type):
    family = geometry_family(source_type)
    if family == 'polygon':
        return 'MULTIPOLYGON'
    if family == 'line':
        return 'MULTILINESTRING'
    if family == 'point':
        return 'MULTIPOINT' if 'multi' in norm(source_type) else 'POINT'
    return 'GEOMETRY'


def _geometry_repair_parts(column, source_type):
    source = sql.SQL('{}').format(sql.Identifier(column))
    family = geometry_family(source_type)
    if family == 'polygon':
        valid = sql.SQL('ST_Multi({})').format(source)
        repaired = sql.SQL('ST_Multi(ST_CollectionExtract(ST_MakeValid({}), 3))').format(source)
    elif family == 'line':
        valid = sql.SQL('ST_Multi({})').format(source)
        repaired = sql.SQL('ST_Multi(ST_CollectionExtract(ST_MakeValid({}), 2))').format(source)
    elif family == 'point':
        valid = source
        repaired = sql.SQL('ST_CollectionExtract(ST_MakeValid({}), 1)').format(source)
    else:
        valid = source
        repaired = sql.SQL('ST_MakeValid({})').format(source)
    return source, valid, repaired


def _geometry_safe_predicate(column, source_type):
    # Geometrias realmente irrecuperáveis permanecem na RAW, mas não entram
    # na tabela operacional. Geometrias nulas mantêm o comportamento legado.
    source, _valid, repaired = _geometry_repair_parts(column, source_type)
    return sql.SQL(
        '({g} IS NULL OR ST_IsValid({g}) OR '
        '({r} IS NOT NULL AND NOT ST_IsEmpty({r}) AND ST_IsValid({r})))'
    ).format(g=source, r=repaired)


def _geometry_expr(column, source_type, source_srid, target_srid=None):
    source, valid, repaired = _geometry_repair_parts(column, source_type)

    # A RAW permanece idêntica à geometria recebida. Somente a tabela
    # operacional recebe reparo topológico. Feições não reparáveis ficam na RAW
    # e são excluídas apenas da seleção operacional, com registro no relatório.
    expr = sql.SQL(
        'CASE WHEN {g} IS NULL THEN NULL '
        'WHEN ST_IsValid({g}) THEN {valid} ELSE {repaired} END'
    ).format(g=source, valid=valid, repaired=repaired)

    if target_srid and source_srid and int(target_srid) != int(source_srid):
        expr = sql.SQL('ST_Transform({}, {})').format(expr, sql.Literal(int(target_srid)))
    return expr

def _geometry_typmod(type_name, srid):
    allowed = {'MULTIPOLYGON', 'MULTILINESTRING', 'MULTIPOINT', 'POLYGON', 'LINESTRING', 'POINT', 'GEOMETRY'}
    name = str(type_name or 'GEOMETRY').upper()
    if name not in allowed:
        name = 'GEOMETRY'
    if srid:
        return sql.SQL('geometry({}, {})').format(sql.SQL(name), sql.Literal(int(srid)))
    return sql.SQL('geometry({})').format(sql.SQL(name))


def _adapt_destination_geometry(schema, table, desired_type, desired_srid, preserve_rows):
    meta = geometry_metadata(schema, table)
    if not meta:
        return
    current_type = str(meta.get('type') or '').upper()
    current_srid = meta.get('srid')
    desired_type = str(desired_type or 'GEOMETRY').upper()

    same_type = current_type == desired_type or current_type == 'GEOMETRY'
    same_srid = not desired_srid or not current_srid or int(current_srid) == int(desired_srid)
    if same_type and same_srid:
        return

    current_family = geometry_family(current_type)
    desired_family = geometry_family(desired_type)
    if preserve_rows and current_family and desired_family and current_family != desired_family:
        raise ValueError(
            f'A tabela operacional existente possui geometria {current_type}, incompatível com a nova geometria {desired_type}.'
        )

    geom = sql.Identifier(meta['column'])
    using = sql.SQL('{}').format(geom)
    if desired_family in {'polygon', 'line'}:
        using = sql.SQL('ST_Multi({})').format(using)
    if desired_srid and current_srid and int(current_srid) != int(desired_srid):
        using = sql.SQL('ST_Transform({}, {})').format(using, sql.Literal(int(desired_srid)))

    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL('ALTER TABLE {}.{} ALTER COLUMN {} TYPE {} USING {}').format(
                sql.Identifier(schema),
                sql.Identifier(table),
                geom,
                _geometry_typmod(desired_type, desired_srid),
                using,
            )
        )




def _operational_sql_type(logical_type):
    mapping = {
        'numeric': 'numeric',
        'integer': 'integer',
        'year': 'integer',
        'date': 'date',
        'date_flexible': 'date',
        'text': 'text',
    }
    return mapping.get(str(logical_type or 'text').lower(), 'text')


def _ensure_operational_columns(cursor, dest_ident, spec):
    """Evolui a tabela operacional sem destruir dados existentes.

    As tabelas geoespaciais do CONFRONTA são criadas e mantidas pelo pipeline,
    não por migrations Django. Quando uma fonte oficial ganha novos atributos,
    adicionar um FieldSpec não pode exigir DROP/TRUNCATE de toda a tabela.
    Esta rotina acrescenta somente colunas canônicas ausentes e preserva todas
    as linhas já publicadas. Os novos campos serão preenchidos na próxima
    importação da base correspondente.
    """
    for field in spec.fields:
        cursor.execute(
            sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {}').format(
                dest_ident,
                sql.Identifier(field.canonical),
                sql.SQL(_operational_sql_type(field.sql_type)),
            )
        )
    for key, _value in spec.fixed_values:
        cursor.execute(
            sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} text').format(
                dest_ident,
                sql.Identifier(key),
            )
        )
    cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS fonte text').format(dest_ident))
    cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS dataset_slug text').format(dest_ident))
    cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS data_importacao timestamptz').format(dest_ident))


def build_normalized_table(schema, raw_table, dest_table, spec, append_partition=False, partition_context=None, merge_by_key=None):
    columns = table_columns(schema, raw_table)
    source_geom = geometry_metadata(schema, raw_table)
    if not source_geom:
        raise ValueError('A tabela RAW não possui coluna geométrica reconhecida pelo PostGIS.')

    source_type = source_geom['type']
    source_srid = source_geom['srid']
    desired_type = _promoted_geometry_type(source_type)
    existing_dest_geom = geometry_metadata(schema, dest_table)

    # Em tabelas consolidadas (PRODES), preservamos o SRID já adotado e
    # transformamos somente a nova partição quando a fonte oficial mudar o CRS.
    # Em replace_table, a tabela será truncada e poderá acompanhar o novo SRID.
    if (append_partition or merge_by_key) and existing_dest_geom and existing_dest_geom.get('srid'):
        target_srid = existing_dest_geom['srid']
    else:
        target_srid = source_srid

    select_parts = []
    insert_columns = []
    mapped = {}
    date_profiles = {}
    for field in spec.fields:
        source = find_alias(columns, field.aliases)
        if field.required and source is None:
            raise ValueError(f'Campo lógico obrigatório não foi mapeado: {field.canonical}.')
        mapped[field.canonical] = source
        date_order = None
        if field.sql_type == 'date_flexible' and source is not None:
            profile = analyze_flexible_date_column(schema, raw_table, source)
            date_order = profile.get('preferencia_ambiguos')
            profile.update(
                analyze_date_conversion(
                    schema, raw_table, source, field.sql_type, date_order=date_order
                )
            )
            date_profiles[field.canonical] = profile
        elif field.sql_type == 'date' and source is not None:
            date_profiles[field.canonical] = {
                'campo_origem': source,
                'politica': 'ISO ou DD/MM com calendario validado',
                **analyze_date_conversion(schema, raw_table, source, field.sql_type),
            }
        select_parts.append(
            sql.SQL('{} AS {}').format(
                cast_expr(source, field.sql_type, date_order=date_order),
                sql.Identifier(field.canonical),
            )
        )
        insert_columns.append(field.canonical)

    for key, value in spec.fixed_values:
        select_parts.append(sql.SQL('%s::text AS {}').format(sql.Identifier(key)))
        insert_columns.append(key)

    partition_context = partition_context or {}
    runtime_values = []
    if partition_context.get('uf') and not merge_by_key:
        select_parts.append(sql.SQL('%s::text AS uf_origem'))
        insert_columns.append('uf_origem')
        runtime_values.append(str(partition_context['uf']).upper())

    geometry_expr = _geometry_expr(source_geom['column'], source_type, source_srid, target_srid)
    select_parts.extend([
        sql.SQL('%s::text AS fonte'),
        sql.SQL('%s::text AS dataset_slug'),
        sql.SQL('CURRENT_TIMESTAMP AS data_importacao'),
        sql.SQL('{} AS geometry').format(geometry_expr),
    ])
    insert_columns.extend(['fonte','dataset_slug','data_importacao','geometry'])

    params = [v for _, v in spec.fixed_values] + runtime_values + [str(spec.fonte), spec.slug]
    source_ident = sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(raw_table))
    dest_ident = sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(dest_table))
    column_sql = sql.SQL(',').join(sql.Identifier(c) for c in insert_columns)

    with connection.cursor() as cursor:
        # A tabela operacional é estável. O ID é gerado pelo PostgreSQL para permanecer
        # único inclusive na tabela consolidada PRODES, onde cada dataset substitui
        # somente sua partição lógica.
        cursor.execute(
            sql.SQL('CREATE TABLE IF NOT EXISTS {} AS SELECT {} FROM {} WHERE false').format(
                dest_ident, sql.SQL(',').join(select_parts), source_ident
            ),
            params,
        )
        _ensure_operational_columns(cursor, dest_ident, spec)
        cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS id bigserial').format(dest_ident))
        cursor.execute(
            sql.SQL('CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (id)').format(
                sql.Identifier(f'idx_{dest_table[:45]}_id'), dest_ident
            )
        )

        legacy_unpartitioned_rows_removed = 0
        registros_substituidos_por_chave = 0
        registros_substituidos_uf = 0
        registros_substituidos_ufs_vizinhas = 0
        if merge_by_key:
            # SICAR operacional permanece em uma tabela nacional por camada.
            # Quando o lote informa uma UF confirmada, a nova carga representa o
            # snapshot mensal daquele estado: substituímos somente as linhas cujo
            # COD_IMOVEL pertence à UF, preservando integralmente os outros 26 estados.
            current_dest = geometry_metadata(schema, dest_table)
            if current_dest and geometry_family(current_dest['type']) == geometry_family(desired_type):
                _adapt_destination_geometry(
                    schema, dest_table, desired_type,
                    current_dest.get('srid') or target_srid, preserve_rows=True,
                )
            if merge_by_key not in mapped or mapped.get(merge_by_key) is None:
                raise ValueError(f'Campo de consolidação SICAR não foi localizado: {merge_by_key}.')
            cursor.execute(sql.SQL('ALTER TABLE {} DROP COLUMN IF EXISTS uf_origem').format(dest_ident))
            source_key = sql.Identifier(mapped[merge_by_key])
            dest_key = sql.Identifier(merge_by_key)
            state_uf = str(partition_context.get('uf') or '').strip().upper()
            if state_uf:
                # O arquivo estadual pode conter CARs de UFs vizinhas em áreas de
                # divisa. A UF administrativa continua sendo um snapshot completo:
                # removemos integralmente apenas essa partição. Para as demais UFs,
                # removemos somente os COD_IMOVEL presentes no arquivo recebido e
                # os reinserimos abaixo. Assim não apagamos o restante de AL/PB/etc.
                # e também não criamos duplicidade para os CARs de fronteira.
                cursor.execute(
                    sql.SQL(
                        'DELETE FROM {dest} AS d USING ('
                        'SELECT DISTINCT trim({source_key}::text) AS chave FROM {source} '
                        "WHERE {source_key} IS NOT NULL AND trim({source_key}::text) <> '' "
                        "AND upper(left(trim({source_key}::text), 2)) <> %s "
                        "AND trim({source_key}::text) ~* '^[A-Z]{{2}}-'"
                        ') AS s WHERE trim(d.{dest_key}::text) = s.chave'
                    ).format(
                        dest=dest_ident, source=source_ident,
                        source_key=source_key, dest_key=dest_key,
                    ),
                    [state_uf],
                )
                registros_substituidos_ufs_vizinhas = (
                    cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
                )
                cursor.execute(
                    sql.SQL(
                        "DELETE FROM {dest} WHERE upper(left(trim({dest_key}::text), 2))=%s "
                        "AND trim({dest_key}::text) ~* '^[A-Z]{{2}}-'"
                    ).format(dest=dest_ident, dest_key=dest_key),
                    [state_uf],
                )
                registros_substituidos_uf = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
            else:
                # Compatibilidade com importações manuais/legadas sem UF: atualiza
                # somente os CARs presentes no arquivo, comportamento histórico.
                cursor.execute(
                    sql.SQL(
                        'DELETE FROM {dest} AS d USING ('
                        'SELECT DISTINCT trim({source_key}::text) AS chave FROM {source} '
                        "WHERE {source_key} IS NOT NULL AND trim({source_key}::text) <> ''"
                        ') AS s WHERE trim(d.{dest_key}::text) = s.chave'
                    ).format(dest=dest_ident, source=source_ident, source_key=source_key, dest_key=dest_key)
                )
                registros_substituidos_por_chave = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        elif append_partition:
            # Antes do INSERT, promove Polygon->MultiPolygon / Line->MultiLineString
            # na tabela existente quando isso puder ser feito sem mudar a família.
            current_dest = geometry_metadata(schema, dest_table)
            desired_dest_type = desired_type
            if current_dest and geometry_family(current_dest['type']) == geometry_family(desired_type):
                if geometry_family(desired_type) in {'polygon', 'line'}:
                    desired_dest_type = desired_type
                else:
                    desired_dest_type = current_dest['type']
                _adapt_destination_geometry(
                    schema,
                    dest_table,
                    desired_dest_type,
                    current_dest.get('srid') or target_srid,
                    preserve_rows=True,
                )

            if partition_context.get('uf'):
                # Migração segura do legado: tabelas SICAR antigas não tinham UF de
                # origem. Linhas sem partição não podem coexistir com a carga nacional,
                # pois não seria possível substituí-las seletivamente por estado.
                cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS uf_origem text').format(dest_ident))
                cursor.execute(sql.SQL('SELECT COUNT(*) FROM {} WHERE uf_origem IS NULL').format(dest_ident))
                legacy_unpartitioned_rows_removed = cursor.fetchone()[0]
                if legacy_unpartitioned_rows_removed:
                    cursor.execute(sql.SQL('DELETE FROM {} WHERE uf_origem IS NULL').format(dest_ident))
                cursor.execute(
                    sql.SQL('DELETE FROM {} WHERE uf_origem=%s').format(dest_ident),
                    [str(partition_context['uf']).upper()],
                )
            else:
                fixed = dict(spec.fixed_values)
                cursor.execute(
                    sql.SQL('DELETE FROM {} WHERE bioma=%s AND tipo_prodes=%s').format(dest_ident),
                    [fixed['bioma'], fixed['tipo_prodes']],
                )
        else:
            cursor.execute(sql.SQL('TRUNCATE TABLE {} RESTART IDENTITY').format(dest_ident))
            # Como não restam linhas antigas, podemos acompanhar de forma segura a
            # mudança de Polygon/MultiPolygon e de SRID publicada pela fonte oficial.
            _adapt_destination_geometry(
                schema,
                dest_table,
                desired_type,
                target_srid,
                preserve_rows=False,
            )

        safe_geometry = _geometry_safe_predicate(source_geom['column'], source_type)
        cursor.execute(
            sql.SQL('INSERT INTO {} ({}) SELECT {} FROM {} WHERE {}').format(
                dest_ident, column_sql, sql.SQL(',').join(select_parts), source_ident, safe_geometry
            ),
            params,
        )
        operational_rows_inserted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        cursor.execute(
            sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} USING GIST (geometry)').format(
                sql.Identifier(f'idx_{dest_table[:45]}_geom'), dest_ident
            )
        )
        if 'cod_imovel' in mapped:
            cursor.execute(
                sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} (cod_imovel)').format(
                    sql.Identifier(f'idx_{dest_table[:45]}_cod'), dest_ident
                )
            )
            cursor.execute(
                sql.SQL(
                    'CREATE INDEX IF NOT EXISTS {} ON {} ((upper(left(trim(cod_imovel::text), 2))))'
                ).format(
                    sql.Identifier(f'idx_{dest_table[:43]}_ufcar'), dest_ident
                )
            )
        if partition_context.get('uf') and not merge_by_key:
            cursor.execute(
                sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} (uf_origem)').format(
                    sql.Identifier(f'idx_{dest_table[:43]}_uf'), dest_ident
                )
            )

    return {
        'mapeamento_campos': mapped,
        'normalizacao_datas': date_profiles,
        'particao': partition_context or {},
        'linhas_legadas_sem_uf_removidas': legacy_unpartitioned_rows_removed,
        'consolidacao_por_chave': merge_by_key or '',
        'registros_substituidos_por_chave': registros_substituidos_por_chave,
        'registros_substituidos_uf': registros_substituidos_uf,
        'registros_substituidos_ufs_vizinhas': registros_substituidos_ufs_vizinhas,
        'tabela_operacional': f'{schema}.{dest_table}',
        'registros_inseridos_operacional': operational_rows_inserted,
        'geometria_normalizada': {
            'origem': source_type,
            'destino': desired_type,
            'srid_origem': source_srid,
            'srid_operacional': target_srid,
        },
    }
