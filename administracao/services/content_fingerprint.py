import hashlib

from django.db import connection
from psycopg import sql


def fingerprint_staging_content(schema, table, geometry_column):
    """Fingerprint SHA-256, independente da ordem das linhas, do conteúdo em staging.

    Ignora identificadores técnicos comuns criados por drivers GIS e inclui a
    geometria em EWKB. O objetivo é detectar conteúdo equivalente mesmo quando
    o ZIP foi recomprimido e, portanto, possui outro SHA-256 de arquivo.
    """
    excluded = ['ogc_fid', 'id']
    if geometry_column:
        excluded.append(str(geometry_column))

    relation = sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))
    json_expr = sql.SQL('to_jsonb(t) - %s::text[]')
    if geometry_column:
        geometry_expr = sql.SQL(
            "COALESCE(encode(ST_AsEWKB(t.{}), 'hex'), '')"
        ).format(sql.Identifier(geometry_column))
    else:
        geometry_expr = sql.SQL("''")

    query = sql.SQL(
        "SELECT md5(({json_expr})::text || '|' || {geometry_expr}) AS row_hash "
        "FROM {relation} AS t ORDER BY row_hash"
    ).format(json_expr=json_expr, geometry_expr=geometry_expr, relation=relation)

    digest = hashlib.sha256()
    rows = 0
    with connection.cursor() as cursor:
        cursor.execute(query, [excluded])
        while True:
            batch = cursor.fetchmany(10000)
            if not batch:
                break
            for (row_hash,) in batch:
                digest.update(str(row_hash or '').encode('ascii'))
                digest.update(b'\n')
                rows += 1
    digest.update(f'rows={rows}'.encode('ascii'))
    return {'sha256': digest.hexdigest(), 'registros': rows}
