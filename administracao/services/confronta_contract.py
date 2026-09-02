from django.db import connection, transaction


def _exists(cursor, qualified):
    cursor.execute('SELECT to_regclass(%s)', [qualified])
    return cursor.fetchone()[0] is not None


def _column_exists(cursor, schema, table, column):
    cursor.execute(
        'SELECT 1 FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name=%s',
        [schema, table, column],
    )
    return cursor.fetchone() is not None


def ensure_confronta_analysis_contract():
    """Cria o contrato SQL estável consumido pelo CONFRONTA principal.

    O Manage é o único responsável pela ingestão. O SaaS cliente consulta estas
    funções e recebe somente ocorrências que realmente interceptam o CAR
    pesquisado, já com a geometria da feição e da sobreposição positiva.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute('CREATE SCHEMA IF NOT EXISTS confronta_api')
        has_car = _exists(cursor, 'dados_sicar.sicar_imoveis')
        has_ibama = _exists(cursor, 'dados_ibama.ibama_embargo')
        has_sigef = _exists(cursor, 'dados_incra.sigef_parcela')
        has_snci = _exists(cursor, 'dados_incra.snci_imovel')

        if has_car and has_ibama:
            cursor.execute(
                """
                CREATE OR REPLACE FUNCTION confronta_api.alertas_ibama_por_car(p_cod_imovel text)
                RETURNS TABLE (
                    seq_tad integer,
                    numero_termo text,
                    situacao text,
                    data_embargo date,
                    area_embargada_ha numeric,
                    area_sobreposicao_ha numeric,
                    percentual_car numeric,
                    processo text,
                    auto_infracao text,
                    municipio text,
                    codigo_municipio text,
                    uf text,
                    fonte text,
                    ultima_sincronizacao timestamptz,
                    geom_alerta geometry,
                    geom_intersecao geometry
                ) LANGUAGE sql STABLE AS $$
                    WITH car AS (
                        SELECT geometry,
                               NULLIF(ST_Area(geometry::geography), 0) AS area_m2
                        FROM dados_sicar.sicar_imoveis
                        WHERE cod_imovel = p_cod_imovel
                        LIMIT 1
                    ), hits AS (
                        SELECT e.*, c.area_m2,
                               ST_Multi(
                                   ST_CollectionExtract(
                                       ST_Intersection(c.geometry, e.geometry), 3
                                   )
                               ) AS inter
                        FROM car c
                        JOIN dados_ibama.ibama_embargo e
                          ON e.geometry && c.geometry
                         AND ST_Intersects(e.geometry, c.geometry)
                    ), positive_hits AS (
                        SELECT h.*,
                               ST_Area(h.inter::geography) AS area_inter_m2
                        FROM hits h
                        WHERE h.inter IS NOT NULL
                          AND NOT ST_IsEmpty(h.inter)
                          AND ST_Dimension(h.inter) = 2
                    )
                    SELECT h.seq_tad,
                           h.numero_embargo,
                           COALESCE(h.status_normalizado, 'A_VERIFICAR'),
                           h.data_embargo,
                           h.area_embargada_ha,
                           round((h.area_inter_m2 / 10000.0)::numeric, 4),
                           round(((h.area_inter_m2 / h.area_m2) * 100.0)::numeric, 4),
                           h.processo,
                           h.auto_infracao,
                           h.municipio,
                           h.codigo_municipio,
                           h.uf,
                           'IBAMA'::text,
                           h.data_importacao,
                           h.geometry,
                           h.inter
                    FROM positive_hits h
                    WHERE h.area_inter_m2 > 0
                    ORDER BY h.area_inter_m2 DESC;
                $$
                """
            )

        if has_ibama:
            detail_tables = {
                'itens': 'embargo_item',
                'coordenadas': 'embargo_coordenada',
                'decisoes_judiciais': 'embargo_decisao_judicial',
                'enquadramentos': 'embargo_enquadramento',
                'historico': 'embargo_historico',
            }
            detail_expr = {}
            for key, table in detail_tables.items():
                if _exists(cursor, f'dados_ibama.{table}') and _column_exists(cursor, 'dados_ibama', table, 'seq_tad'):
                    detail_expr[key] = (
                        f"(SELECT COALESCE(jsonb_agg(to_jsonb(x) - 'id'), '[]'::jsonb) "
                        f"FROM dados_ibama.{table} x WHERE x.seq_tad::text = p_seq_tad::text)"
                    )
                else:
                    detail_expr[key] = "'[]'::jsonb"
            cursor.execute(
                f"""
                CREATE OR REPLACE FUNCTION confronta_api.detalhes_ibama(p_seq_tad integer)
                RETURNS jsonb LANGUAGE sql STABLE AS $$
                    SELECT jsonb_build_object(
                        'natureza', 'ALERTA_PARA_PLANEJAMENTO',
                        'fonte', 'IBAMA',
                        'termo', COALESCE((
                            SELECT to_jsonb(e) - 'geometry'
                            FROM dados_ibama.ibama_embargo e
                            WHERE e.seq_tad = p_seq_tad
                            LIMIT 1
                        ), '{{}}'::jsonb),
                        'itens', {detail_expr['itens']},
                        'coordenadas', {detail_expr['coordenadas']},
                        'decisoes_judiciais', {detail_expr['decisoes_judiciais']},
                        'enquadramentos', {detail_expr['enquadramentos']},
                        'historico', {detail_expr['historico']}
                    );
                $$
                """
            )

        if has_car and (has_sigef or has_snci):
            unions = []
            if has_sigef:
                unions.append(
                    """
                    SELECT 'SIGEF'::text AS sistema,
                           s.parcela_co::text AS identificador,
                           COALESCE(s.status, s.situacao, '')::text AS situacao,
                           s.nome_area::text AS nome,
                           NULL::text AS municipio,
                           s.codigo_municipio::text AS codigo_municipio,
                           s.uf_origem::text AS uf,
                           round((ST_Area(s.geometry::geography) / 10000.0)::numeric, 4) AS area_fonte_ha,
                           s.data_importacao AS ultima_sincronizacao,
                           s.geometry AS geom_alerta,
                           ST_Multi(ST_CollectionExtract(ST_Intersection(c.geometry, s.geometry), 3)) AS inter,
                           c.area_m2
                    FROM car c
                    JOIN dados_incra.sigef_parcela s
                      ON s.geometry && c.geometry
                     AND ST_Intersects(s.geometry, c.geometry)
                    """
                )
            if has_snci:
                unions.append(
                    """
                    SELECT 'SNCI'::text AS sistema,
                           n.num_certif::text AS identificador,
                           'CERTIFICADO'::text AS situacao,
                           n.nome_imovel::text AS nome,
                           NULL::text AS municipio,
                           NULL::text AS codigo_municipio,
                           COALESCE(n.uf, n.uf_origem)::text AS uf,
                           COALESCE(n.area_ha, round((ST_Area(n.geometry::geography) / 10000.0)::numeric, 4)) AS area_fonte_ha,
                           n.data_importacao AS ultima_sincronizacao,
                           n.geometry AS geom_alerta,
                           ST_Multi(ST_CollectionExtract(ST_Intersection(c.geometry, n.geometry), 3)) AS inter,
                           c.area_m2
                    FROM car c
                    JOIN dados_incra.snci_imovel n
                      ON n.geometry && c.geometry
                     AND ST_Intersects(n.geometry, c.geometry)
                    """
                )
            union_sql = '\nUNION ALL\n'.join(unions)
            cursor.execute(
                f"""
                CREATE OR REPLACE FUNCTION confronta_api.alertas_incra_por_car(p_cod_imovel text)
                RETURNS TABLE (
                    sistema text,
                    identificador text,
                    situacao text,
                    nome text,
                    municipio text,
                    codigo_municipio text,
                    uf text,
                    area_fonte_ha numeric,
                    area_sobreposicao_ha numeric,
                    percentual_car numeric,
                    fonte text,
                    ultima_sincronizacao timestamptz,
                    geom_alerta geometry,
                    geom_intersecao geometry
                ) LANGUAGE sql STABLE AS $$
                    WITH car AS (
                        SELECT geometry,
                               NULLIF(ST_Area(geometry::geography), 0) AS area_m2
                        FROM dados_sicar.sicar_imoveis
                        WHERE cod_imovel = p_cod_imovel
                        LIMIT 1
                    ), hits AS (
                        {union_sql}
                    ), positive_hits AS (
                        SELECT h.*,
                               ST_Area(h.inter::geography) AS area_inter_m2
                        FROM hits h
                        WHERE h.inter IS NOT NULL
                          AND NOT ST_IsEmpty(h.inter)
                          AND ST_Dimension(h.inter) = 2
                    )
                    SELECT h.sistema, h.identificador, h.situacao, h.nome,
                           h.municipio, h.codigo_municipio, h.uf,
                           h.area_fonte_ha,
                           round((h.area_inter_m2 / 10000.0)::numeric, 4),
                           round(((h.area_inter_m2 / h.area_m2) * 100.0)::numeric, 4),
                           'INCRA'::text,
                           h.ultima_sincronizacao,
                           h.geom_alerta,
                           h.inter
                    FROM positive_hits h
                    WHERE h.area_inter_m2 > 0
                    ORDER BY h.area_inter_m2 DESC;
                $$
                """
            )
