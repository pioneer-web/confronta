from datetime import date
from django.db import connection
from psycopg import sql

from .exceptions import GISValidationError
from .field_matching import find_matching_field

DEFAULT_PRODES_START_YEAR = 2019
DEFAULT_PRODES_START_DATE = date(2019, 8, 1)


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
    if spec.fonte_slug != 'prodes':
        return {}

    start_year = normalize_prodes_start_year(start_year)

    start_date = (
        DEFAULT_PRODES_START_DATE
        if start_year == DEFAULT_PRODES_START_YEAR
        else date(start_year, 1, 1)
    )

    columns = _table_columns(schema, table)

    year_field_spec = next(
        (field for field in spec.fields if field.canonical == 'year'),
        None,
    )
    year_aliases = (
        year_field_spec.aliases
        if year_field_spec
        else ('year', 'ano', 'year_prodes')
    )
    year_column = find_matching_field(columns, year_aliases)

    date_field_spec = next(
        (field for field in spec.fields if field.canonical == 'image_date'),
        None,
    )
    date_aliases = (
        date_field_spec.aliases
        if date_field_spec
        else ('image_date', 'data_imagem')
    )
    date_column = find_matching_field(columns, date_aliases)

    if not year_column:
        raise GISValidationError(
            'Campo year/ano do PRODES não localizado.'
        )

    if not date_column:
        raise GISValidationError(
            'Campo image_date/data_imagem do PRODES não localizado. '
            'A importação foi bloqueada para evitar corte temporal incorreto.'
        )

    table_ident = sql.SQL('{}.{}').format(
        sql.Identifier(schema),
        sql.Identifier(table),
    )

    year_ident = sql.Identifier(year_column)
    date_ident = sql.Identifier(date_column)

    year_expr = sql.SQL(
        "CASE WHEN trim({field}::text) ~ '^[0-9]{{4}}([.]0+)?$' "
        "THEN trim({field}::text)::numeric::integer ELSE NULL END"
    ).format(field=year_ident)

    date_expr = sql.SQL(
        "CASE "
        "WHEN pg_input_is_valid(left(trim({field}::text), 10), 'date') "
        "THEN left(trim({field}::text), 10)::date "
        "ELSE NULL END"
    ).format(field=date_ident)

    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                'SELECT COUNT(*), '
                'COUNT(*) FILTER (WHERE {date_expr} IS NULL), '
                'COUNT(*) FILTER (WHERE {date_expr} < %s), '
                'COUNT(*) FILTER (WHERE {date_expr} >= %s AND {year_expr} IS NOT NULL), '
                'COUNT(*) FILTER (WHERE {year_expr} IS NULL), '
                'MIN({date_expr}), MAX({date_expr}), '
                'MIN({year_expr}), MAX({year_expr}) '
                'FROM {table}'
            ).format(
                date_expr=date_expr,
                year_expr=year_expr,
                table=table_ident,
            ),
            [start_date, start_date],
        )

        (
            total,
            invalid_date,
            discarded,
            retained,
            invalid_year,
            min_date,
            max_date,
            min_year,
            max_year,
        ) = cursor.fetchone()

        if not retained:
            raise GISValidationError(
                f'Nenhuma ocorrência PRODES atende ao corte de '
                f'{start_date.strftime("%d/%m/%Y")} ou posterior.'
            )

        cursor.execute(
            sql.SQL(
                'DELETE FROM {table} '
                'WHERE {date_expr} IS NULL '
                'OR {date_expr} < %s '
                'OR {year_expr} IS NULL'
            ).format(
                table=table_ident,
                date_expr=date_expr,
                year_expr=year_expr,
            ),
            [start_date],
        )

        removed = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

    return {
        'aplicado': True,
        'ano_inicial': start_year,
        'data_inicial': start_date.isoformat(),
        'campo_ano': year_column,
        'campo_data': date_column,
        'registros_originais': int(total or 0),
        'registros_descartados_antes_da_data': int(discarded or 0),
        'registros_descartados_antes_do_ano': int(discarded or 0),
        'registros_removidos_staging': int(removed or 0),
        'registros_mantidos': int(retained or 0),
        'registros_data_invalida': int(invalid_date or 0),
        'registros_ano_invalido': int(invalid_year or 0),
        'tem_pendencias': bool(invalid_date or invalid_year),
        'data_minima_encontrada': min_date.isoformat() if min_date else None,
        'data_maxima_encontrada': max_date.isoformat() if max_date else None,
        'ano_minimo_encontrado': min_year,
        'ano_maximo_encontrado': max_year,
    }
