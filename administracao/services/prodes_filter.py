from django.db import connection
from psycopg import sql

from .exceptions import GISValidationError
from .field_matching import find_matching_field

DEFAULT_PRODES_START_YEAR = 2019


def normalize_prodes_start_year(value, *, default=DEFAULT_PRODES_START_YEAR):
    """Normaliza o corte temporal do PRODES sem permitir anos anteriores à regra do projeto."""
    if value in (None, ""):
        return int(default)
    try:
        year = int(value)
    except (TypeError, ValueError) as exc:
        raise GISValidationError('O ano inicial do PRODES deve ser um número inteiro.') from exc
    if year < DEFAULT_PRODES_START_YEAR:
        raise GISValidationError(
            f'O PRODES do CONFRONTA aceita somente ocorrências a partir de {DEFAULT_PRODES_START_YEAR}.'
        )
    return year


def _table_columns(schema, table):
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT column_name FROM information_schema.columns '
            'WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position',
            [schema, table],
        )
        return [row[0] for row in cursor.fetchall()]


def apply_prodes_year_filter(schema, table, spec, start_year):
    """Filtra o staging PRODES antes de RAW/operação e devolve contagens auditáveis.

    A fonte oficial permanece intacta em disco. O corte é aplicado apenas à cópia
    temporária em staging. Registros com ano inválido são contabilizados e removidos
    somente da cópia de staging; os registros válidos continuam o fluxo. Se o campo
    de ano não existir ou o filtro produzir uma partição vazia, a promoção é bloqueada.
    """
    if spec.fonte_slug != 'prodes':
        return {}

    start_year = normalize_prodes_start_year(start_year)
    columns = _table_columns(schema, table)
    year_field_spec = next((field for field in spec.fields if field.canonical == 'year'), None)
    aliases = year_field_spec.aliases if year_field_spec else ('year', 'ano', 'year_prodes')
    year_column = find_matching_field(columns, aliases)
    if not year_column:
        raise GISValidationError(
            'O campo de ano do PRODES não foi localizado no staging. A base anterior foi preservada.'
        )

    table_ident = sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))
    field_ident = sql.Identifier(year_column)
    year_expr = sql.SQL(
        "CASE WHEN trim({field}::text) ~ '^[0-9]{{4}}([.]0+)?$' "
        "THEN trim({field}::text)::numeric::integer ELSE NULL END"
    ).format(field=field_ident)

    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                'SELECT COUNT(*), '
                'COUNT(*) FILTER (WHERE {year_expr} IS NULL), '
                'COUNT(*) FILTER (WHERE {year_expr} < %s), '
                'COUNT(*) FILTER (WHERE {year_expr} >= %s), '
                'MIN({year_expr}), MAX({year_expr}) '
                'FROM {table}'
            ).format(year_expr=year_expr, table=table_ident),
            [start_year, start_year],
        )
        total, invalid, discarded, retained, min_year, max_year = cursor.fetchone()

        if not retained:
            raise GISValidationError(
                f'Nenhuma ocorrência PRODES atende ao corte de {start_year} ou posterior. '
                'A promoção foi bloqueada para evitar substituir a partição ativa por uma base vazia.'
            )

        invalid_values = []
        if invalid:
            cursor.execute(
                sql.SQL(
                    'SELECT DISTINCT trim({field}::text) '
                    'FROM {table} WHERE {year_expr} IS NULL LIMIT 20'
                ).format(field=field_ident, table=table_ident, year_expr=year_expr)
            )
            invalid_values = [row[0] for row in cursor.fetchall()]

        cursor.execute(
            sql.SQL('DELETE FROM {table} WHERE {year_expr} IS NULL OR {year_expr} < %s').format(
                table=table_ident, year_expr=year_expr
            ),
            [start_year],
        )
        removed = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

    return {
        'aplicado': True,
        'ano_inicial': start_year,
        'campo_ano': year_column,
        'registros_originais': int(total or 0),
        'registros_descartados_antes_do_ano': int(discarded or 0),
        'registros_removidos_staging': int(removed or 0),
        'registros_mantidos': int(retained or 0),
        'registros_ano_invalido': int(invalid or 0),
        'tem_pendencias': bool(invalid),
        'valores_ano_invalidos_amostra': invalid_values,
        'ano_minimo_encontrado': min_year,
        'ano_maximo_encontrado': max_year,
    }
