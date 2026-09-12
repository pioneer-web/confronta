import json
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import DatabaseError, connection
from psycopg import sql

from administracao.constants import FonteDados
from administracao.models import CamadaImportada

logger = logging.getLogger(__name__)


class CamadaIndisponivel(Exception):
    pass


class ImovelNaoEncontrado(Exception):
    pass


class ImovelDuplicado(Exception):
    pass


class CamadaExportacaoInvalida(Exception):
    pass


class ExportacaoMuitoGrande(Exception):
    pass


class RepositorioTerritorial:
    """Leitura territorial do Módulo 2.

    IMPORTANTE — MÓDULO 2:
    este repositório consome somente tabelas operacionais já promovidas pelo
    Módulo 1. Ele não altera RAW, staging, CamadaImportada ou dados oficiais.
    """

    SCHEMA_SICAR = 'dados_sicar'
    TABELA_IMOVEIS = 'sicar_imoveis'
    DATASET_IMOVEIS = 'sicar-perimetros'

    LIMITE_FEICOES_POR_CAMADA = 2500
    LIMITE_EXPORTACAO_POR_CAMADA = 10000
    LIMITE_INTERSECOES_EXTERNAS = 2000
    LIMITE_OUTROS_CARS = 1000

    CAMADAS_SICAR = {
        'app': {
            'dataset_slug': 'sicar-app',
            'tabela': 'sicar_app',
            'label': 'APP',
            'campos': ('tipo', 'area_ha'),
        },
        'reserva_legal': {
            'dataset_slug': 'sicar-reserva-legal',
            'tabela': 'sicar_reserva_legal',
            'label': 'Reserva Legal',
            'campos': ('situacao', 'tipo', 'area_ha'),
        },
        'vegetacao_nativa': {
            'dataset_slug': 'sicar-vegetacao-nativa',
            'tabela': 'sicar_vegetacao_nativa',
            'label': 'Vegetação Nativa',
            'campos': ('area_ha',),
        },
        'area_consolidada': {
            'dataset_slug': 'sicar-area-consolidada',
            'tabela': 'sicar_area_consolidada',
            'label': 'Área Consolidada',
            'campos': ('area_ha',),
        },
        'area_pousio': {
            'dataset_slug': 'sicar-area-pousio',
            'tabela': 'sicar_area_pousio',
            'label': 'Área de Pousio',
            'campos': ('area_ha',),
        },
        'hidrografia': {
            'dataset_slug': 'sicar-hidrografia',
            'tabela': 'sicar_hidrografia',
            'label': 'Hidrografia',
            'campos': ('tipo', 'nome'),
        },
        'servidao_administrativa': {
            'dataset_slug': 'sicar-servidao-administrativa',
            'tabela': 'sicar_servidao_administrativa',
            'label': 'Servidão Administrativa',
            'campos': ('tipo', 'area_ha'),
        },
        'uso_restrito': {
            'dataset_slug': 'sicar-uso-restrito',
            'tabela': 'sicar_uso_restrito',
            'label': 'Área de Uso Restrito',
            'campos': ('tipo', 'area_ha'),
        },
    }

    # Tolerância única para considerar interseção de área real. O valor de
    # 1 m² foi aprovado para eliminar simples contato de borda.
    TOLERANCIA_INTERSECAO_M2 = 1.0

    ANALISES_EXTERNAS = {
        'ibama': {
            'fonte': FonteDados.IBAMA,
            'schema': 'dados_ibama',
            'tabela': 'ibama_embargo',
            'label': 'Embargo IBAMA',
            'campos': (
                'embargo_id', 'seq_tad', 'numero_embargo', 'serie_embargo',
                'auto_infracao', 'serie_auto', 'processo', 'data_embargo',
                'situacao', 'tipo_area', 'bioma', 'municipio', 'uf', 'nome_imovel',
                'unidade_ibama', 'descricao_infracao', 'descricao_termo',
                'area_desmatada_informada_ha', 'area_embargo_informada_ha',
                'data_ultima_alteracao', 'data_base', 'data_importacao',
            ),
        },
        'prodes': {
            'fonte': FonteDados.PRODES,
            'schema': 'dados_prodes',
            'tabela': 'prodes_ocorrencia',
            'label': 'INPE / PRODES',
            'campos': (
                'uuid', 'fid_origem', 'state', 'path_row', 'main_class', 'class_name',
                'def_cloud', 'julian_day', 'image_date', 'year', 'area_km', 'scene_id',
                'source', 'satellite', 'sensor', 'bioma', 'tipo_prodes',
                'dataset_slug', 'data_importacao',
            ),
        },
        'assentamentos': {
            'fonte': FonteDados.INCRA,
            'schema': 'dados_incra',
            'tabela': 'incra_assentamentos',
            'label': 'Projetos de Assentamento — INCRA',
            'campos': (
                'codigo', 'nome', 'modalidade', 'situacao', 'fase', 'municipio', 'uf',
                'area_ha', 'area_calculada_ha', 'capacidade_familias',
                'quantidade_familias', 'data_criacao', 'forma_obtencao',
                'data_obtencao', 'descricao', 'data_importacao',
            ),
        },
        'quilombolas': {
            'fonte': FonteDados.INCRA,
            'schema': 'dados_incra',
            'tabela': 'incra_areas_quilombolas',
            'label': 'Territórios Quilombolas — INCRA',
            'campos': (
                'identificacao', 'codigo_quilombola', 'codigo_sr', 'processo', 'nome',
                'situacao', 'fase', 'municipio', 'uf', 'area_ha', 'area_calculada_ha',
                'quantidade_familias', 'data_publicacao', 'data_titulacao',
                'data_decreto', 'codigo_sipra', 'responsavel', 'esfera', 'observacao',
                'data_importacao',
            ),
        },
        'icmbio_embargo': {
            'fonte': FonteDados.ICMBIO,
            'schema': 'dados_icmbio',
            'tabela': 'icmbio_embargo',
            'label': 'Embargo ICMBio',
            'include_full_geometry': True,
            'campos': (
                'embargo_id', 'numero_embargo', 'data_embargo', 'situacao',
                'area_ha', 'data_base',
            ),
        },
        'funai': {
            'fonte': FonteDados.FUNAI,
            'schema': 'dados_funai',
            'tabela': 'funai_terras_indigenas',
            'label': 'Terra Indígena — FUNAI',
            'include_full_geometry': True,
            'campos': (
                'terrai_cod', 'terrai_nom', 'etnia_nome', 'municipio', 'uf_sigla',
                'superficie', 'fase_ti', 'modalidade', 'coordenacao_regional',
                'faixa_fronteira', 'data_atualizacao',
            ),
        },
        'sicor_wkt': {
            'fonte': FonteDados.SICOR,
            'schema': 'dados_sicor',
            'tabela': 'sicor_glebas_wkt',
            'include_full_geometry': True,
            'label': 'SICOR / Glebas financiadas — WKT',
            'geometry_column': 'geom',
            'campos': (
                '_ano_arquivo', 'ref_bacen', 'nu_ordem', 'nu_indice',
            ),
        },
        'sicor_contratadas': {
            'fonte': FonteDados.SICOR,
            'schema': 'dados_sicor',
            'tabela': 'sicor_glebas_contratadas',
            'include_full_geometry': True,
            'label': 'SICOR / Glebas contratadas',
            'geometry_column': 'geom',
            'campos': (
                'ref_bacen', 'nu_ordem', 'nu_identificador', 'nu_indice_gleba',
                'qtd_pontos', 'altitude_min', 'altitude_max', 'area_ha_calculada',
            ),
        },
        'apa_cnuc': {
            'fonte': FonteDados.CNUC,
            'schema': 'dados_cnuc',
            'tabela': 'cnuc_unidade_conservacao',
            'label': 'APA / Unidade de Conservação — CNUC',
            'campos': (
                'uc_id', 'codigo_cnuc', 'wdpa_pid', 'nome_uc', 'nome_abreviado',
                'categoria_manejo', 'grupo_manejo', 'esfera', 'municipio', 'uf',
                'orgao_gestor', 'situacao', 'ano_criacao', 'ato_criacao', 'outro_ato',
                'plano_manejo', 'conselho_gestor', 'categoria_iucn',
                'qualidade_poligono', 'programa_gestao', 'area_ha', 'area_ato_ha',
                'amazonia_ha', 'caatinga_ha', 'cerrado_ha', 'mata_atlantica_ha',
                'pampa_ha', 'pantanal_ha', 'marinho_ha', 'data_base', 'data_importacao',
            ),
            'filtro_sql': sql.SQL(
                "AND (upper(trim(coalesce(e.categoria_manejo::text, ''))) = 'APA' "
                "OR upper(coalesce(e.categoria_manejo::text, '')) LIKE '%%PROTE%%AMBIENTAL%%')"
            ),
        },
        'apa_icmbio': {
            'fonte': FonteDados.ICMBIO,
            'schema': 'dados_icmbio',
            'tabela': 'icmbio_unidade_conservacao_federal',
            'label': 'APA / Unidade de Conservação — ICMBio',
            'campos': (
                'uc_id', 'nome_uc', 'codigo_cnuc', 'ano_criacao', 'area_ha', 'perimetro_m',
                'ato_criacao', 'esfera', 'grupo_manejo', 'biomas', 'gerencia_regional',
                'fuso', 'demarcacao', 'escala', 'bioma_predominante', 'categoria_iucn',
                'uf', 'categoria_manejo', 'sigla_categoria', 'dominio', 'data_importacao',
            ),
            'filtro_sql': sql.SQL(
                "AND (upper(trim(coalesce(e.sigla_categoria::text, ''))) = 'APA' "
                "OR upper(coalesce(e.categoria_manejo::text, '')) LIKE '%%PROTE%%AMBIENTAL%%')"
            ),
        },
    }

    @staticmethod
    def _camada_ativa(dataset_slug, tabela):
        return CamadaImportada.objects.filter(
            fonte=FonteDados.SICAR,
            dataset_slug=dataset_slug,
            schema_banco=RepositorioTerritorial.SCHEMA_SICAR,
            nome_tabela=tabela,
            status=CamadaImportada.Status.ATIVA,
        ).exists()

    @staticmethod
    def _camada_externa_ativa(fonte, schema, tabela):
        return CamadaImportada.objects.filter(
            fonte=fonte,
            schema_banco=schema,
            nome_tabela=tabela,
            status=CamadaImportada.Status.ATIVA,
        ).exists()

    @staticmethod
    def _table_columns(schema, tabela):
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT column_name FROM information_schema.columns '
                'WHERE table_schema=%s AND table_name=%s',
                [schema, tabela],
            )
            return {row[0] for row in cursor.fetchall()}

    @staticmethod
    def _table_srid(schema, tabela):
        """Obtém o SRID real da tabela operacional sem alterar seus dados.

        A primeira fonte continua sendo ``geometry_columns``. Algumas tabelas
        operacionais consolidadas podem, porém, manter geometrias válidas com
        SRID definido nas próprias feições sem expor um SRID útil nessa view
        de metadados. Nesse caso fazemos fallback para ``ST_SRID`` sobre uma
        geometria real não nula/não vazia.

        O fallback é somente leitura e usa identificadores SQL validados pelo
        driver, preservando a arquitetura e evitando SRID presumido.
        """
        geometry_column = None

        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT f_geometry_column, srid FROM geometry_columns '
                'WHERE f_table_schema=%s AND f_table_name=%s '
                'ORDER BY f_geometry_column LIMIT 1',
                [schema, tabela],
            )
            row = cursor.fetchone()

            if row:
                geometry_column = row[0]
                try:
                    metadata_srid = int(row[1])
                except (TypeError, ValueError):
                    metadata_srid = 0
                if metadata_srid > 0:
                    return metadata_srid

            if not geometry_column:
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s "
                    "AND udt_name='geometry' "
                    "ORDER BY CASE WHEN column_name='geometry' THEN 0 ELSE 1 END, ordinal_position "
                    "LIMIT 1",
                    [schema, tabela],
                )
                geom_row = cursor.fetchone()
                geometry_column = geom_row[0] if geom_row else None

            if not geometry_column:
                return None

            tabela_ident = sql.Identifier(schema, tabela)
            geom_ident = sql.Identifier(geometry_column)
            cursor.execute(
                sql.SQL(
                    'SELECT ST_SRID({geom}) '
                    'FROM {tabela} '
                    'WHERE {geom} IS NOT NULL '
                    'AND NOT ST_IsEmpty({geom}) '
                    'AND ST_SRID({geom}) > 0 '
                    'LIMIT 1'
                ).format(geom=geom_ident, tabela=tabela_ident)
            )
            srid_row = cursor.fetchone()

        if not srid_row or not srid_row[0]:
            return None
        try:
            value = int(srid_row[0])
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @staticmethod
    def _geom_geojson_sql(column='geometry'):
        geom = sql.Identifier(column)
        return sql.SQL(
            "CASE WHEN {g} IS NULL THEN NULL "
            "WHEN ST_SRID({g}) = 4326 THEN ST_AsGeoJSON(ST_Force2D({g}), 6) "
            "WHEN ST_SRID({g}) > 0 THEN ST_AsGeoJSON(ST_Force2D(ST_Transform({g}, 4326)), 6) "
            "ELSE NULL END"
        ).format(g=geom)

    def buscar_imovel_por_car(self, car):
        if not self._camada_ativa(self.DATASET_IMOVEIS, self.TABELA_IMOVEIS):
            raise CamadaIndisponivel('A base operacional de perímetros do SICAR não está disponível.')

        tabela = sql.Identifier(self.SCHEMA_SICAR, self.TABELA_IMOVEIS)
        geom_expr = self._geom_geojson_sql()
        query = sql.SQL(
            "SELECT cod_imovel::text, area_total_ha, uf::text, municipio::text, "
            "codigo_municipio::text, modulos_fiscais, tipo_imovel::text, "
            "situacao_car::text, condicao::text, fonte::text, dataset_slug::text, "
            "data_importacao, {geom} AS geojson "
            "FROM {tabela} "
            "WHERE cod_imovel = %s "
            "LIMIT 2"
        ).format(geom=geom_expr, tabela=tabela)

        with connection.cursor() as cursor:
            cursor.execute(query, [car])
            rows = cursor.fetchall()
            if not rows:
                fallback = sql.SQL(
                    "SELECT cod_imovel::text, area_total_ha, uf::text, municipio::text, "
                    "codigo_municipio::text, modulos_fiscais, tipo_imovel::text, "
                    "situacao_car::text, condicao::text, fonte::text, dataset_slug::text, "
                    "data_importacao, {geom} AS geojson "
                    "FROM {tabela} "
                    "WHERE upper(trim(cod_imovel::text)) = upper(trim(%s)) "
                    "LIMIT 2"
                ).format(geom=geom_expr, tabela=tabela)
                cursor.execute(fallback, [car])
                rows = cursor.fetchall()

        if not rows:
            raise ImovelNaoEncontrado('Nenhum imóvel foi localizado para o CAR informado.')
        if len(rows) > 1:
            raise ImovelDuplicado('Foram localizados múltiplos perímetros para o mesmo CAR. A base precisa ser revisada.')

        row = rows[0]
        geometry = json.loads(row[12]) if row[12] else None
        return {
            'cod_imovel': row[0],
            'area_total_ha': self._numero(row[1]),
            'uf': row[2] or '',
            'municipio': row[3] or '',
            'codigo_municipio': row[4] or '',
            'modulos_fiscais': self._numero(row[5]),
            'tipo_imovel': row[6] or '',
            'situacao_car': row[7] or '',
            'condicao': row[8] or '',
            'fonte': row[9] or '',
            'dataset_slug': row[10] or '',
            'data_importacao': self._serializar(row[11]),
            'geometry': geometry,
        }

    def buscar_cars_por_ponto(self, latitude, longitude, *, limite=20):
        """Localiza CARs que contêm/intersectam um ponto WGS84.

        A consulta é somente leitura e usa o SRID real da tabela operacional.
        Em áreas com CARs sobrepostos podem existir vários resultados; a ordem
        prioriza o menor imóvel, que tende a representar a feição mais específica.
        """
        if not self._camada_ativa(self.DATASET_IMOVEIS, self.TABELA_IMOVEIS):
            raise CamadaIndisponivel('A base operacional de perímetros do SICAR não está disponível.')

        srid = self._table_srid(self.SCHEMA_SICAR, self.TABELA_IMOVEIS)
        if not srid:
            raise CamadaIndisponivel('A base operacional do SICAR não possui SRID válido para consulta espacial.')

        tabela = sql.Identifier(self.SCHEMA_SICAR, self.TABELA_IMOVEIS)
        query = sql.SQL(
            "WITH entrada AS ("
            "  SELECT CASE WHEN {srid} = 4326 "
            "    THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326) "
            "    ELSE ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), {srid}) END AS geom"
            ") "
            "SELECT i.cod_imovel::text, i.area_total_ha, i.uf::text, i.municipio::text "
            "FROM {tabela} i CROSS JOIN entrada e "
            "WHERE i.geometry IS NOT NULL AND NOT ST_IsEmpty(i.geometry) "
            "  AND i.geometry && e.geom "
            "  AND ST_Intersects(ST_MakeValid(i.geometry), e.geom) "
            "ORDER BY i.area_total_ha ASC NULLS LAST, i.cod_imovel::text ASC "
            "LIMIT %s"
        ).format(srid=sql.Literal(srid), tabela=tabela)

        with connection.cursor() as cursor:
            cursor.execute(query, [longitude, latitude, longitude, latitude, max(1, int(limite))])
            rows = cursor.fetchall()

        return [
            {
                'cod_imovel': row[0],
                'area_total_ha': self._numero(row[1]),
                'uf': row[2] or '',
                'municipio': row[3] or '',
            }
            for row in rows
        ]

    def buscar_cars_por_geojson(self, geometria, *, limite=20):
        """Localiza CARs com interseção de área real com Polygon/MultiPolygon WGS84."""
        if not self._camada_ativa(self.DATASET_IMOVEIS, self.TABELA_IMOVEIS):
            raise CamadaIndisponivel('A base operacional de perímetros do SICAR não está disponível.')

        srid = self._table_srid(self.SCHEMA_SICAR, self.TABELA_IMOVEIS)
        if not srid:
            raise CamadaIndisponivel('A base operacional do SICAR não possui SRID válido para consulta espacial.')

        try:
            geometria_json = json.dumps(geometria, ensure_ascii=False, separators=(',', ':'))
        except (TypeError, ValueError) as exc:
            raise ValueError('Geometria inválida para consulta.') from exc

        tabela = sql.Identifier(self.SCHEMA_SICAR, self.TABELA_IMOVEIS)
        query = sql.SQL(
            "WITH entrada_wgs AS ("
            "  SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326) AS geom"
            "), entrada AS ("
            "  SELECT CASE WHEN {srid}=4326 THEN geom ELSE ST_Transform(geom, {srid}) END AS geom "
            "  FROM entrada_wgs"
            "), candidatos AS ("
            "  SELECT i.cod_imovel::text AS cod_imovel, i.area_total_ha, i.uf::text AS uf, "
            "         i.municipio::text AS municipio, "
            "         ST_CollectionExtract(ST_MakeValid(ST_Intersection(ST_MakeValid(i.geometry), e.geom)), 3) AS inter_geom, "
            "         ST_Area((ST_Transform(ST_MakeValid(i.geometry), 4326))::geography) AS area_car_m2 "
            "  FROM {tabela} i CROSS JOIN entrada e "
            "  WHERE i.geometry IS NOT NULL AND NOT ST_IsEmpty(i.geometry) "
            "    AND i.geometry && e.geom "
            "    AND ST_Intersects(ST_MakeValid(i.geometry), e.geom)"
            "), metricas AS ("
            "  SELECT *, ST_Area((ST_Transform(inter_geom, 4326))::geography) AS area_inter_m2 "
            "  FROM candidatos WHERE inter_geom IS NOT NULL AND NOT ST_IsEmpty(inter_geom)"
            ") "
            "SELECT cod_imovel, area_total_ha, uf, municipio, area_inter_m2 / 10000.0 AS area_sobreposta_ha, "
            "       CASE WHEN area_car_m2 > 0 THEN (area_inter_m2 / area_car_m2) * 100.0 ELSE NULL END AS percentual_car "
            "FROM metricas "
            "WHERE area_inter_m2 > %s "
            "ORDER BY area_inter_m2 DESC, area_total_ha ASC NULLS LAST, cod_imovel ASC "
            "LIMIT %s"
        ).format(srid=sql.Literal(srid), tabela=tabela)

        with connection.cursor() as cursor:
            cursor.execute(query, [geometria_json, self.TOLERANCIA_INTERSECAO_M2, max(1, int(limite))])
            rows = cursor.fetchall()

        return [
            {
                'cod_imovel': row[0],
                'area_total_ha': self._numero(row[1]),
                'uf': row[2] or '',
                'municipio': row[3] or '',
                'area_sobreposta_ha': self._numero(row[4]),
                'percentual_car': self._numero(row[5]),
            }
            for row in rows
        ]

    def buscar_camadas_sicar(self, car):
        resultado = {}
        for chave, cfg in self.CAMADAS_SICAR.items():
            if not self._camada_ativa(cfg['dataset_slug'], cfg['tabela']):
                resultado[chave] = {
                    'label': cfg['label'],
                    'disponivel': False,
                    'total_area_ha': None,
                    'features': [],
                    'truncada': False,
                }
                continue
            resultado[chave] = self._buscar_camada(
                car,
                cfg,
                limite=self.LIMITE_FEICOES_POR_CAMADA,
            )
        return resultado

    def buscar_analises_externas(self, car):
        resultado = {}
        resultados_apa = {}
        resultados_sicor = {}

        for chave, cfg in self.ANALISES_EXTERNAS.items():
            if not self._camada_externa_ativa(cfg['fonte'], cfg['schema'], cfg['tabela']):
                item = self._resultado_externo_indisponivel(
                    cfg['label'], 'Base ainda não está ativa no Módulo 1.'
                )
            else:
                srid = self._table_srid(cfg['schema'], cfg['tabela'])
                if not srid:
                    item = self._resultado_externo_indisponivel(
                        cfg['label'], 'A tabela operacional não possui SRID válido para confronto espacial.'
                    )
                else:
                    try:
                        item = self._buscar_intersecoes_externas(car, cfg, srid)
                    except DatabaseError:
                        logger.exception('Falha ao consultar análise externa %s para o CAR %s.', chave, car)
                        item = self._resultado_externo_indisponivel(
                            cfg['label'], 'Não foi possível consultar esta base territorial neste momento.'
                        )

            if chave.startswith('apa_'):
                resultados_apa[chave] = item
            elif chave.startswith('sicor_'):
                resultados_sicor[chave] = item
            else:
                resultado[chave] = item

        resultado['apa'] = self._combinar_apa_fontes(
            resultados_apa.get('apa_cnuc'),
            resultados_apa.get('apa_icmbio'),
        )
        resultado['sicor'] = self._combinar_sicor_fontes(
            resultados_sicor.get('sicor_wkt'),
            resultados_sicor.get('sicor_contratadas'),
        )
        return resultado

    @staticmethod
    def _chave_sicor(registro, origem='SICOR'):
        ref_bacen = str(registro.get('ref_bacen') or '').strip().upper()
        nu_ordem = str(registro.get('nu_ordem') or '').strip()
        indice = str(registro.get('nu_indice') or registro.get('nu_indice_gleba') or '').strip()
        if ref_bacen and nu_ordem:
            return f'{ref_bacen}:{nu_ordem}:{indice or "SEM_INDICE"}'
        return f'{origem}:{ref_bacen}:{nu_ordem}:{indice}:{id(registro)}'

    def _buscar_dados_operacoes_sicor(self, registros):
        """Complementa glebas SICOR com dados não sensíveis da operação básica.

        A camada espacial continua sendo a evidência para o alerta. O vínculo com
        a operação usa somente ``ref_bacen`` + ``nu_ordem`` e não expõe dados de
        mutuário/CPF/CNPJ. A ausência da tabela complementar não bloqueia o mapa.
        """
        if not registros:
            return {}
        schema = 'dados_sicor'
        tabela = 'sicor_operacao_basica'
        if not self._camada_externa_ativa(FonteDados.SICOR, schema, tabela):
            return {}

        existentes = self._table_columns(schema, tabela)
        if not {'ref_bacen', 'nu_ordem'} <= existentes:
            return {}

        refs = sorted({str(r.get('ref_bacen') or '').strip() for r in registros if str(r.get('ref_bacen') or '').strip()})
        if not refs:
            return {}

        opcionais = (
            '_ano_arquivo', 'dt_emissao', 'dt_vencimento', 'cd_estado',
            'cd_fonte_recurso', 'cd_empreendimento', 'cd_programa',
            'vl_parc_credito', 'vl_area_financ', 'vl_area_informada', 'vl_juros',
        )
        campos = tuple(c for c in opcionais if c in existentes)
        select_campos = [sql.Identifier('ref_bacen'), sql.Identifier('nu_ordem')]
        select_campos.extend(sql.Identifier(c) for c in campos)
        order_extra = sql.SQL('')
        if '_ano_arquivo' in existentes:
            order_extra = sql.SQL(', {} DESC NULLS LAST').format(sql.Identifier('_ano_arquivo'))
        if '_numero_linha' in existentes:
            order_extra += sql.SQL(', {} DESC NULLS LAST').format(sql.Identifier('_numero_linha'))

        query = sql.SQL(
            'SELECT {} FROM {} WHERE ref_bacen = ANY(%s) '
            'ORDER BY ref_bacen, nu_ordem{}'
        ).format(
            sql.SQL(', ').join(select_campos),
            sql.Identifier(schema, tabela),
            order_extra,
        )
        with connection.cursor() as cursor:
            cursor.execute(query, [refs])
            rows = cursor.fetchall()

        resultado = {}
        for row in rows:
            ref = str(row[0] or '').strip().upper()
            ordem = str(row[1] or '').strip()
            chave = (ref, ordem)
            if chave in resultado:
                continue
            dados = {}
            for idx, campo in enumerate(campos, start=2):
                valor = row[idx]
                if valor in (None, ''):
                    continue
                if campo.startswith('vl_'):
                    dados[campo] = self._numero(valor)
                else:
                    dados[campo] = self._serializar(valor)
            resultado[chave] = dados
        return resultado

    def _combinar_sicor_fontes(self, wkt, contratadas):
        wkt = wkt or self._resultado_externo_indisponivel(
            'SICOR / Glebas financiadas — WKT', 'Base SICOR WKT indisponível.'
        )
        contratadas = contratadas or self._resultado_externo_indisponivel(
            'SICOR / Glebas contratadas', 'Base SICOR de glebas contratadas indisponível.'
        )

        if not wkt.get('disponivel') and not contratadas.get('disponivel'):
            return self._resultado_externo_indisponivel(
                'SICOR / Crédito Rural',
                'Nenhuma camada espacial de glebas SICOR está ativa para o confronto.'
            )

        merged = {}
        feature_by_key = {}
        origem_por_chave = {}
        for origem, fonte in (('WKT', wkt), ('COORDENADAS', contratadas)):
            if not fonte.get('disponivel'):
                continue
            registros = fonte.get('registros', [])
            features = fonte.get('features', [])
            for index, registro in enumerate(registros):
                item = dict(registro)
                item['origem_gleba_sicor'] = 'Glebas WKT' if origem == 'WKT' else 'Glebas por coordenadas'
                if item.get('_ano_arquivo') not in (None, ''):
                    item['ano_sicor'] = item.pop('_ano_arquivo')
                indice = item.get('nu_indice')
                if indice in (None, ''):
                    indice = item.get('nu_indice_gleba')
                if indice not in (None, ''):
                    item['indice_gleba'] = indice
                chave = self._chave_sicor(item, origem)

                atual = merged.get(chave)
                if atual is None:
                    merged[chave] = item
                    origem_por_chave[chave] = origem
                else:
                    # WKT é a representação espacial preferencial quando os dois
                    # produtos descrevem a mesma operação/gleba. A fonte por pontos
                    # apenas completa atributos ausentes.
                    if origem == 'WKT' and origem_por_chave.get(chave) != 'WKT':
                        anterior = atual
                        merged[chave] = item
                        for campo, valor in anterior.items():
                            if merged[chave].get(campo) in (None, '') and valor not in (None, ''):
                                merged[chave][campo] = valor
                        origem_por_chave[chave] = 'WKT'
                    else:
                        for campo, valor in item.items():
                            if atual.get(campo) in (None, '') and valor not in (None, ''):
                                atual[campo] = valor

                if index < len(features):
                    feature = features[index]
                    if chave not in feature_by_key or origem == 'WKT':
                        feature_by_key[chave] = feature

        registros = list(merged.values())
        operacoes = self._buscar_dados_operacoes_sicor(registros)
        for item in registros:
            ref = str(item.get('ref_bacen') or '').strip().upper()
            ordem = str(item.get('nu_ordem') or '').strip()
            dados = operacoes.get((ref, ordem), {})
            if dados:
                for campo, valor in dados.items():
                    if campo == '_ano_arquivo':
                        item.setdefault('ano_operacao', valor)
                    else:
                        item.setdefault(campo, valor)
                if not item.get('ano_sicor') and dados.get('_ano_arquivo') not in (None, ''):
                    item['ano_sicor'] = dados['_ano_arquivo']

        features = []
        for chave, item in merged.items():
            feature = feature_by_key.get(chave)
            if not feature:
                continue
            props = dict(feature.get('properties') or {})
            props.update(item)
            features.append({
                'type': 'Feature',
                'properties': props,
                'geometry': feature.get('geometry'),
            })

        origens = {origem_por_chave.get(chave) for chave in merged}
        if merged and origens <= {'WKT'}:
            area_unica = wkt.get('area_unica_sobreposta_ha')
        elif merged and origens <= {'COORDENADAS'}:
            area_unica = contratadas.get('area_unica_sobreposta_ha')
        else:
            # Não somamos áreas entre representações oficiais diferentes para
            # evitar dupla contagem quando uma mesma gleba exista nos dois produtos.
            area_unica = None

        return {
            'label': 'SICOR / Crédito Rural',
            'disponivel': bool(wkt.get('disponivel') or contratadas.get('disponivel')),
            'quantidade': len(registros),
            'features': features,
            'registros': registros,
            'truncada': bool(wkt.get('truncada') or contratadas.get('truncada')),
            'motivo': '',
            'area_unica_sobreposta_ha': area_unica,
            'fontes_espaciais': sorted({r.get('origem_gleba_sicor') for r in registros if r.get('origem_gleba_sicor')}),
        }

    @staticmethod
    def _chave_uc(registro, fonte):
        codigo = str(registro.get('codigo_cnuc') or registro.get('uc_id') or '').strip().upper()
        if codigo:
            return f'COD:{codigo}'
        nome = str(registro.get('nome_uc') or '').strip().casefold()
        return f'{fonte}:NOME:{nome}' if nome else f'{fonte}:SEM_CHAVE:{id(registro)}'

    def _combinar_apa_fontes(self, cnuc, icmbio):
        cnuc = cnuc or self._resultado_externo_indisponivel('APA / Unidade de Conservação — CNUC', 'Base CNUC indisponível.')
        icmbio = icmbio or self._resultado_externo_indisponivel('APA / Unidade de Conservação — ICMBio', 'Base ICMBio indisponível.')

        if not cnuc.get('disponivel') and not icmbio.get('disponivel'):
            return self._resultado_externo_indisponivel(
                'APA / Unidade de Conservação',
                'As bases CNUC e ICMBio não estão disponíveis para o confronto.'
            )

        # Mantemos CNUC como fonte administrativa principal. ICMBio complementa
        # UCs federais por código CNUC, sem duplicar a mesma APA no mapa/relatório.
        merged = {}
        feature_by_key = {}

        for fonte, origem in (('CNUC', cnuc), ('ICMBIO', icmbio)):
            registros = origem.get('registros', []) if origem.get('disponivel') else []
            features = origem.get('features', []) if origem.get('disponivel') else []
            for index, registro in enumerate(registros):
                key = self._chave_uc(registro, fonte)
                atual = merged.get(key)
                if atual is None:
                    atual = dict(registro)
                    atual['fontes'] = [fonte]
                    merged[key] = atual
                else:
                    if fonte not in atual['fontes']:
                        atual['fontes'].append(fonte)
                    # Campos CNUC já existentes têm prioridade. O ICMBio somente
                    # completa lacunas ou atributos exclusivos federais.
                    for campo, valor in registro.items():
                        if atual.get(campo) in (None, '') and valor not in (None, ''):
                            atual[campo] = valor

                if index < len(features):
                    feature = features[index]
                    # Geometria CNUC é preferida quando as duas fontes existem.
                    if key not in feature_by_key or fonte == 'CNUC':
                        feature_by_key[key] = feature

        registros_finais = []
        features_finais = []
        for key, registro in merged.items():
            registro['fonte_integrada'] = ' + '.join(registro.pop('fontes', []))
            registros_finais.append(registro)
            feature = feature_by_key.get(key)
            if feature:
                props = dict(feature.get('properties') or {})
                props.update(registro)
                features_finais.append({
                    'type': 'Feature',
                    'properties': props,
                    'geometry': feature.get('geometry'),
                })

        fontes_registros = {registro.get('fonte_integrada') for registro in registros_finais}
        if registros_finais and fontes_registros <= {'CNUC', 'CNUC + ICMBIO'}:
            # Todos os resultados existem no CNUC; usamos a união espacial já
            # calculada nessa fonte e evitamos somar a representação federal.
            area_unica = cnuc.get('area_unica_sobreposta_ha')
        elif registros_finais and fontes_registros <= {'ICMBIO'}:
            area_unica = icmbio.get('area_unica_sobreposta_ha')
        else:
            # Se houver simultaneamente UCs exclusivas de cada fonte, não existe
            # ainda uma união PostGIS canônica entre as duas consultas. Melhor não
            # publicar um total potencialmente incorreto do que somar/deduplicar
            # silenciosamente geometrias de versões oficiais diferentes.
            area_unica = None

        return {
            'label': 'APA / Unidade de Conservação',
            'disponivel': bool(cnuc.get('disponivel') or icmbio.get('disponivel')),
            'quantidade': len(registros_finais),
            'features': features_finais,
            'registros': registros_finais,
            'truncada': bool(cnuc.get('truncada') or icmbio.get('truncada')),
            'motivo': '',
            'area_unica_sobreposta_ha': area_unica,
        }

    def buscar_sobreposicoes_outros_cars(self, car):
        """Localiza interseções de área entre o CAR consultado e outros CARs.

        MÓDULO 2:
        contato apenas de borda não é tratado como sobreposição. A tolerância
        aprovada é centralizada em TOLERANCIA_INTERSECAO_M2.
        """
        label = 'Sobreposição com outros CARs'
        if not self._camada_ativa(self.DATASET_IMOVEIS, self.TABELA_IMOVEIS):
            return self._resultado_externo_indisponivel(label, 'Base de perímetros SICAR indisponível.')

        srid = self._table_srid(self.SCHEMA_SICAR, self.TABELA_IMOVEIS)
        if not srid:
            return self._resultado_externo_indisponivel(label, 'A base de perímetros não possui SRID válido.')

        tabela = sql.Identifier(self.SCHEMA_SICAR, self.TABELA_IMOVEIS)
        limite = self.LIMITE_OUTROS_CARS
        query = sql.SQL(
            "WITH alvo AS ("
            "  SELECT geometry AS geom, "
            "         ST_Area((ST_Transform(ST_MakeValid(geometry), 4326))::geography) AS area_alvo_m2 "
            "  FROM {tabela} WHERE cod_imovel = %s LIMIT 1"
            "), candidatos AS ("
            "  SELECT e.*, a.area_alvo_m2, "
            "         ST_Area((ST_Transform(ST_MakeValid(e.geometry), 4326))::geography) AS area_outro_m2, "
            "         ST_CollectionExtract("
            "             ST_Intersection(ST_MakeValid(e.geometry), ST_MakeValid(a.geom)), 3"
            "         ) AS inter_geom "
            "  FROM {tabela} e CROSS JOIN alvo a "
            "  WHERE e.cod_imovel <> %s "
            "    AND e.geometry IS NOT NULL AND a.geom IS NOT NULL "
            "    AND ST_SRID(e.geometry) = {srid} AND ST_SRID(a.geom) = {srid} "
            "    AND e.geometry && a.geom "
            "    AND ST_Intersects(e.geometry, a.geom)"
            "), metricas AS ("
            "  SELECT *, "
            "         ST_Area((ST_Transform(inter_geom, 4326))::geography) AS area_sobreposta_m2 "
            "  FROM candidatos "
            "  WHERE inter_geom IS NOT NULL AND NOT ST_IsEmpty(inter_geom)"
            ") "
            "SELECT cod_imovel::text, area_total_ha, uf::text, municipio::text, "
            "codigo_municipio::text, modulos_fiscais, tipo_imovel::text, "
            "situacao_car::text, condicao::text, "
            "area_sobreposta_m2 / 10000.0 AS area_sobreposta_ha, "
            "CASE WHEN area_alvo_m2 > 0 THEN (area_sobreposta_m2 / area_alvo_m2) * 100.0 ELSE NULL END AS percentual_car_consultado, "
            "CASE WHEN area_outro_m2 > 0 THEN (area_sobreposta_m2 / area_outro_m2) * 100.0 ELSE NULL END AS percentual_outro_car, "
            "ST_AsGeoJSON(ST_Force2D(ST_Transform(inter_geom, 4326)), 6) AS geojson, "
            "ST_AsGeoJSON(ST_Force2D(ST_Transform(ST_MakeValid(geometry), 4326)), 6) AS geojson_full "
            "FROM metricas "
            "WHERE area_sobreposta_m2 > %s "
            "ORDER BY area_sobreposta_m2 DESC "
            "LIMIT %s"
        ).format(tabela=tabela, srid=sql.Literal(srid))

        with connection.cursor() as cursor:
            cursor.execute(query, [car, car, self.TOLERANCIA_INTERSECAO_M2, limite + 1])
            rows = cursor.fetchall()

        truncada = len(rows) > limite
        rows = rows[:limite]
        features = []
        registros = []
        campos = (
            'cod_imovel', 'area_total_ha', 'uf', 'municipio', 'codigo_municipio',
            'modulos_fiscais', 'tipo_imovel', 'situacao_car', 'condicao',
        )
        for row in rows:
            props = {campo: self._serializar(row[idx]) for idx, campo in enumerate(campos)}
            props['area_sobreposta_ha'] = self._numero(row[9])
            props['percentual_car_consultado'] = self._numero(row[10])
            props['percentual_outro_car'] = self._numero(row[11])
            geometry = json.loads(row[12]) if row[12] else None
            full_geometry = json.loads(row[13]) if len(row) > 13 and row[13] else None
            if full_geometry:
                props['_confronta_full_geometry'] = full_geometry
            registros.append(props.copy())
            if geometry:
                features.append({'type': 'Feature', 'properties': props, 'geometry': geometry})

        return {
            'label': label,
            'disponivel': True,
            'quantidade': len(registros),
            'features': features,
            'registros': registros,
            'truncada': truncada,
            'motivo': '',
        }

    def buscar_camada_para_exportacao(self, car, chave):
        cfg = self.CAMADAS_SICAR.get(chave)
        if not cfg:
            raise CamadaExportacaoInvalida('A camada solicitada não é reconhecida pelo aplicativo.')
        if not self._camada_ativa(cfg['dataset_slug'], cfg['tabela']):
            raise CamadaIndisponivel(f"A camada {cfg['label']} não está disponível para exportação.")

        resultado = self._buscar_camada(
            car,
            cfg,
            limite=self.LIMITE_EXPORTACAO_POR_CAMADA,
            detectar_excesso=True,
        )
        if resultado['truncada']:
            raise ExportacaoMuitoGrande(
                f"A camada {cfg['label']} excede o limite seguro de exportação para uma única operação."
            )
        return resultado

    def _buscar_camada(self, car, cfg, limite, detectar_excesso=False):
        tabela = sql.Identifier(self.SCHEMA_SICAR, cfg['tabela'])
        geom_expr = self._geom_geojson_sql()
        campos = tuple(cfg['campos'])
        select_campos = sql.SQL(', ').join(sql.Identifier(campo) for campo in campos)
        query = sql.SQL(
            "SELECT {campos}, {geom} AS geojson "
            "FROM {tabela} "
            "WHERE cod_imovel = %s "
            "LIMIT %s"
        ).format(campos=select_campos, geom=geom_expr, tabela=tabela)

        limite_consulta = limite + 1
        with connection.cursor() as cursor:
            cursor.execute(query, [car, limite_consulta])
            rows = cursor.fetchall()

        truncada = len(rows) > limite
        rows = rows[:limite]
        features = []
        area_total = Decimal('0')
        encontrou_area = False

        for row in rows:
            props = {}
            for index, campo in enumerate(campos):
                value = row[index]
                props[campo] = self._numero(value) if campo.endswith('_ha') else self._serializar(value)
                if campo.endswith('_ha') and value is not None:
                    try:
                        area_total += Decimal(str(value))
                        encontrou_area = True
                    except Exception:
                        pass
            geometry_raw = row[len(campos)]
            geometry = json.loads(geometry_raw) if geometry_raw else None
            if geometry:
                features.append({
                    'type': 'Feature',
                    'properties': props,
                    'geometry': geometry,
                })

        return {
            'label': cfg['label'],
            'disponivel': True,
            'total_area_ha': float(area_total) if encontrou_area else None,
            'features': features,
            'truncada': truncada if detectar_excesso or truncada else False,
        }

    def _buscar_intersecoes_externas(self, car, cfg, srid):
        tabela_imovel = sql.Identifier(self.SCHEMA_SICAR, self.TABELA_IMOVEIS)
        tabela_externa = sql.Identifier(cfg['schema'], cfg['tabela'])
        colunas_existentes = self._table_columns(cfg['schema'], cfg['tabela'])
        campos = tuple(campo for campo in cfg['campos'] if campo in colunas_existentes)
        campos_sql = sql.SQL(', ').join(sql.Identifier(campo) for campo in campos)
        campos_prefixo = sql.SQL('{}, ').format(campos_sql) if campos else sql.SQL('')
        filtro = cfg.get('filtro_sql') or sql.SQL('')
        geometry_column = cfg.get('geometry_column', 'geometry')
        geometry_sql = sql.Identifier(geometry_column)
        # PRODES: nenhuma classificação interna da fonte é usada para excluir
        # feições do confronto espacial. O Módulo 2 retorna qualquer ocorrência
        # PRODES que possua interseção real com o CAR; a regra temporal de crédito
        # rural (ano >= 2019) é aplicada na camada de serviço, sem apagar histórico.
        limite = self.LIMITE_INTERSECOES_EXTERNAS

        cte = sql.SQL(
            "WITH alvo AS ("
            "  SELECT geometry AS geom, "
            "         ST_Area((ST_Transform(ST_MakeValid(geometry), 4326))::geography) AS area_car_m2 "
            "  FROM {tabela_imovel} WHERE cod_imovel = %s LIMIT 1"
            "), candidatos AS ("
            "  SELECT e.*, a.area_car_m2, "
            "         ST_Area((ST_Transform(ST_MakeValid(e.{geometry}), 4326))::geography) AS area_fonte_m2, "
            "         ST_CollectionExtract(ST_MakeValid(ST_Intersection("
            "             e.{geometry}, CASE WHEN ST_SRID(a.geom) = {srid} THEN a.geom "
            "             WHEN ST_SRID(a.geom) > 0 THEN ST_Transform(a.geom, {srid}) ELSE NULL END"
            "         )), 3) AS inter_geom "
            "  FROM {tabela_externa} e CROSS JOIN alvo a "
            "  WHERE e.{geometry} IS NOT NULL AND a.geom IS NOT NULL "
            "    AND ST_SRID(e.{geometry}) = {srid} AND ST_SRID(a.geom) > 0 "
            "    AND e.{geometry} && CASE WHEN ST_SRID(a.geom) = {srid} THEN a.geom "
            "        WHEN ST_SRID(a.geom) > 0 THEN ST_Transform(a.geom, {srid}) ELSE NULL END "
            "    AND ST_Intersects(e.{geometry}, CASE WHEN ST_SRID(a.geom) = {srid} THEN a.geom "
            "        WHEN ST_SRID(a.geom) > 0 THEN ST_Transform(a.geom, {srid}) ELSE NULL END) "
            "    {filtro}"
            "), metricas AS ("
            "  SELECT *, ST_Area((ST_Transform(inter_geom, 4326))::geography) AS area_sobreposta_m2 "
            "  FROM candidatos "
            "  WHERE inter_geom IS NOT NULL AND NOT ST_IsEmpty(inter_geom)"
            ") "
        ).format(
            tabela_imovel=tabela_imovel,
            tabela_externa=tabela_externa,
            geometry=geometry_sql,
            srid=sql.Literal(srid),
            filtro=filtro,
        )

        full_geometry_sql = (
            sql.SQL(
                "ST_AsGeoJSON(ST_Force2D(ST_Transform("
                "ST_CollectionExtract(ST_MakeValid({geometry}), 3), 4326)), 6)"
            ).format(geometry=geometry_sql)
            if cfg.get('include_full_geometry')
            else sql.SQL("NULL::text")
        )

        query = cte + sql.SQL(
            "SELECT {campos_prefixo}"
            "area_fonte_m2 / 10000.0 AS area_geometria_ha, "
            "area_sobreposta_m2 / 10000.0 AS area_sobreposta_ha, "
            "CASE WHEN area_car_m2 > 0 THEN (area_sobreposta_m2 / area_car_m2) * 100.0 ELSE NULL END AS percentual_car, "
            "CASE WHEN area_fonte_m2 > 0 THEN (area_sobreposta_m2 / area_fonte_m2) * 100.0 ELSE NULL END AS percentual_fonte, "
            "ST_AsGeoJSON(ST_Force2D(ST_Transform(inter_geom, 4326)), 6) AS geojson, "
            "{full_geometry} AS full_geojson "
            "FROM metricas "
            "WHERE area_sobreposta_m2 > %s "
            "ORDER BY area_sobreposta_m2 DESC "
            "LIMIT %s"
        ).format(
            campos_prefixo=campos_prefixo,
            full_geometry=full_geometry_sql,
        )

        with connection.cursor() as cursor:
            cursor.execute(query, [car, self.TOLERANCIA_INTERSECAO_M2, limite + 1])
            rows = cursor.fetchall()

        truncada = len(rows) > limite
        rows = rows[:limite]
        features = []
        registros = []
        for row in rows:
            props = {}
            for index, campo in enumerate(campos):
                value = row[index]
                props[campo] = self._numero(value) if campo.endswith('_ha') or campo in {'area_km', 'def_cloud'} else self._serializar(value)
            offset = len(campos)
            props['area_geometria_ha'] = self._numero(row[offset])
            props['area_sobreposta_ha'] = self._numero(row[offset + 1])
            props['percentual_car'] = self._numero(row[offset + 2])
            props['percentual_fonte'] = self._numero(row[offset + 3])
            geometry_raw = row[offset + 4]
            full_geometry_raw = row[offset + 5]
            geometry = json.loads(geometry_raw) if geometry_raw else None
            full_geometry = json.loads(full_geometry_raw) if full_geometry_raw else None
            registros.append(props.copy())
            if geometry:
                feature_props = props.copy()
                if full_geometry:
                    feature_props['_confronta_full_geometry'] = full_geometry
                features.append({'type': 'Feature', 'properties': feature_props, 'geometry': geometry})

        area_unica = None
        if registros:
            aggregate_query = cte + sql.SQL(
                "SELECT ST_Area((ST_UnaryUnion(ST_Collect(ST_Transform(inter_geom, 4326))))::geography) / 10000.0 "
                "FROM metricas WHERE area_sobreposta_m2 > %s"
            )
            with connection.cursor() as cursor:
                cursor.execute(aggregate_query, [car, self.TOLERANCIA_INTERSECAO_M2])
                row = cursor.fetchone()
                area_unica = self._numero(row[0]) if row and row[0] is not None else None

        return {
            'label': cfg['label'],
            'disponivel': True,
            'quantidade': len(registros),
            'features': features,
            'registros': registros,
            'truncada': truncada,
            'motivo': '',
            'area_unica_sobreposta_ha': area_unica,
            'tolerancia_intersecao_m2': self.TOLERANCIA_INTERSECAO_M2,
        }

    @staticmethod
    def _resultado_externo_indisponivel(label, motivo):
        return {
            'label': label,
            'disponivel': False,
            'quantidade': 0,
            'features': [],
            'registros': [],
            'truncada': False,
            'motivo': motivo,
        }

    @staticmethod
    def _serializar(value):
        if value is None:
            return ''
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return value

    @staticmethod
    def _numero(value):
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError, InvalidOperation):
            return value
