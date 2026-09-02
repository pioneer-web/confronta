import os
import subprocess

from django.db import connection, transaction
from django.utils import timezone
from psycopg import sql

from administracao.constants import FONTE_SCHEMAS
from administracao.models import Alerta, CamadaImportada
from .exceptions import GISValidationError, PromotionError
from .normalization import build_normalized_table
from .schema_drift import format_alert_message, geometry_family


def _db_conn_parts():
    db = connection.settings_dict
    return {
        'host': db['HOST'],
        'port': db['PORT'],
        'dbname': db['NAME'],
        'user': db['USER'],
        'password': db['PASSWORD'],
    }


def _pg_ogr_connection():
    p = _db_conn_parts()
    return f"PG:host={p['host']} port={p['port']} dbname={p['dbname']} user={p['user']}"


def _subprocess_env():
    env = os.environ.copy()
    env['PGPASSWORD'] = _db_conn_parts()['password']
    return env


def create_staging_schema(importacao_id):
    schema = f'stg_{str(importacao_id).replace("-", "")[:20]}'
    with connection.cursor() as c:
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    return schema


def drop_schema(schema):
    with connection.cursor() as c:
        c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


def build_ogr2ogr_command(layer, staging_schema, target_table='incoming'):
    """Monta a importação RAW de forma tolerante aos metadados das fontes oficiais.

    PRECISION=NO impede que width/precision do DBF virem NUMERIC(width,scale)
    excessivamente restritivos no PostgreSQL. PROMOTE_TO_MULTI elimina a
    incompatibilidade Polygon x MultiPolygon (e LineString x MultiLineString)
    sem descartar feições.
    """
    command = [
        'ogr2ogr',
        '--config', 'OGR_PG_ENABLE_METADATA', 'NO',
        '--config', 'PG_USE_COPY', 'YES',
        '-f', 'PostgreSQL',
        _pg_ogr_connection(),
        '-nln', f'{staging_schema}.{target_table}',
        '-lco', 'GEOMETRY_NAME=geom',
        '-lco', 'PRECISION=NO',
    ]
    if geometry_family(layer.get('geometry_type')) in {'polygon', 'line'}:
        command.extend(['-nlt', 'PROMOTE_TO_MULTI'])
    if layer.get('encoding_override'):
        # Mantém no ogr2ogr o mesmo charset confirmado durante a inspeção do
        # Shapefile. Usamos tanto a open option quanto SHAPE_ENCODING porque
        # bases oficiais brasileiras podem declarar charset em sidecars legados
        # (.cst) que nem todas as versões do GDAL aplicam automaticamente.
        encoding = str(layer['encoding_override'])
        command.extend([
            '--config', 'SHAPE_ENCODING', encoding,
            '-oo', f'ENCODING={encoding}',
        ])
    command.extend([layer['dataset_path'], layer['layer_name']])
    return command


def _decode_subprocess_output(data):
    if data is None:
        return ''
    if isinstance(data, str):
        return data
    for encoding in ('utf-8', 'cp1252', 'latin-1'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def import_layer_to_staging(layer, staging_schema, target_table='incoming'):
    command = build_ogr2ogr_command(layer, staging_schema, target_table=target_table)
    # Não usamos text=True aqui: alguns pacotes oficiais possuem metadados/DBF
    # Windows-1252/Latin-1 e o próprio stderr do GDAL pode conter bytes que não
    # formam UTF-8 válido. Decodificar de forma tolerante evita que um simples
    # acento no diagnóstico derrube uma importação que o ogr2ogr conseguiria ler.
    proc = subprocess.run(
        command,
        env=_subprocess_env(),
        capture_output=True,
        check=False,
    )
    stderr = _decode_subprocess_output(proc.stderr)
    if proc.returncode != 0:
        # O stderr do PostgreSQL pode incluir a linha COPY inteira (incluindo WKB
        # da geometria). Em geometrias grandes, guardar apenas o final escondia
        # justamente a mensagem principal do PostgreSQL, que costuma aparecer no
        # início (encoding, tipo, SRID, coluna etc.). Preservamos início + fim.
        if len(stderr) > 7000:
            diagnostic = stderr[:3500] + "\n...[trecho volumoso omitido]...\n" + stderr[-3500:]
        else:
            diagnostic = stderr
        raise GISValidationError(
            f'ogr2ogr falhou ao importar {layer["layer_name"]}: {diagnostic}'
        )
    layer['ogr2ogr_adaptacoes'] = {
        'precision_no': True,
        'promote_to_multi': '-nlt' in command,
        'encoding_override': layer.get('encoding_override'),
        'text_encoding_repair': layer.get('text_encoding_repair') or {},
    }
    return inspect_staging_table(staging_schema, target_table)


def _geometry_metadata(schema, table):
    with connection.cursor() as c:
        c.execute(
            'SELECT f_geometry_column,type,srid FROM geometry_columns '
            'WHERE f_table_schema=%s AND f_table_name=%s ORDER BY f_geometry_column LIMIT 1',
            [schema, table],
        )
        return c.fetchone()


def _repair_geometry_expression(geometry_column, geometry_type):
    """Expressão PostGIS para reparo seguro sem alterar a tabela RAW.

    A geometria original permanece no staging/RAW. Esta expressão é usada
    apenas para verificar se as geometrias inválidas são reparáveis. A mesma
    política é aplicada na normalização da tabela operacional.
    """
    g = sql.Identifier(geometry_column)
    family = geometry_family(geometry_type)
    if family == 'polygon':
        repaired = sql.SQL('ST_Multi(ST_CollectionExtract(ST_MakeValid({g}), 3))').format(g=g)
        valid = sql.SQL('ST_Multi({g})').format(g=g)
    elif family == 'line':
        repaired = sql.SQL('ST_Multi(ST_CollectionExtract(ST_MakeValid({g}), 2))').format(g=g)
        valid = sql.SQL('ST_Multi({g})').format(g=g)
    elif family == 'point':
        repaired = sql.SQL('ST_CollectionExtract(ST_MakeValid({g}), 1)').format(g=g)
        valid = g
    else:
        repaired = sql.SQL('ST_MakeValid({g})').format(g=g)
        valid = g
    return sql.SQL(
        'CASE WHEN {g} IS NULL THEN NULL '
        'WHEN ST_IsValid({g}) THEN {valid} ELSE {repaired} END'
    ).format(g=g, valid=valid, repaired=repaired)


def inspect_geometry_repairability(schema, table):
    """Conta inválidas, reparáveis e pendências sem alterar a RAW.

    As geometrias não reparáveis continuam na RAW para rastreabilidade e são
    apenas excluídas da tabela operacional. O relatório guarda uma amostra de
    identificadores e motivos, evitando JSON gigantes em bases muito grandes.
    """
    gm = _geometry_metadata(schema, table)
    if not gm:
        return {
            'detectadas': 0,
            'reparaveis': 0,
            'nao_reparaveis': 0,
            'metodo': 'ST_MakeValid',
            'raw_preservada': True,
            'politica_operacional': 'somente_validas_ou_reparaveis',
            'amostras_pendencias': [],
        }
    g, gtype, _srid = gm
    t = sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))
    gi = sql.Identifier(g)
    repaired = _repair_geometry_expression(g, gtype)
    with connection.cursor() as c:
        c.execute(
            sql.SQL(
                'SELECT '
                'COUNT(*) FILTER (WHERE {g} IS NOT NULL AND NOT ST_IsValid({g})), '
                'COUNT(*) FILTER (WHERE {g} IS NOT NULL AND NOT ST_IsValid({g}) '
                'AND {r} IS NOT NULL AND NOT ST_IsEmpty({r}) AND ST_IsValid({r})) '
                'FROM {t}'
            ).format(g=gi, r=repaired, t=t)
        )
        invalid, repairable = c.fetchone()

        # Identificadores úteis para auditoria, quando existirem na fonte.
        c.execute(
            'SELECT column_name FROM information_schema.columns '
            'WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position',
            [schema, table],
        )
        columns = [row[0] for row in c.fetchall()]
        preferred = ['cod_imovel', 'uuid', 'id', 'fid', 'ogc_fid']
        id_columns = [name for name in preferred if name in columns][:2]
        identifier_exprs = [
            sql.SQL("COALESCE({}::text, '')").format(sql.Identifier(name))
            for name in id_columns
        ]
        if identifier_exprs:
            identifier_sql = sql.SQL("concat_ws(' | ', {})").format(sql.SQL(', ').join(identifier_exprs))
        else:
            identifier_sql = sql.SQL("''::text")

        c.execute(
            sql.SQL(
                'SELECT {identifier}, ST_IsValidReason({g})::text, '
                "COALESCE(ST_GeometryType({r})::text, ''), "
                'CASE WHEN {r} IS NULL THEN true ELSE ST_IsEmpty({r}) END '
                'FROM {t} '
                'WHERE {g} IS NOT NULL AND NOT ST_IsValid({g}) '
                'AND NOT ({r} IS NOT NULL AND NOT ST_IsEmpty({r}) AND ST_IsValid({r})) '
                'LIMIT 50'
            ).format(identifier=identifier_sql, g=gi, r=repaired, t=t)
        )
        samples = [
            {
                'identificador': row[0] or '',
                'motivo_original': row[1] or '',
                'tipo_apos_reparo': row[2] or '',
                'reparo_vazio_ou_nulo': bool(row[3]),
            }
            for row in c.fetchall()
        ]

    non_repairable = max(0, invalid - repairable)
    return {
        'detectadas': invalid,
        'reparaveis': repairable,
        'nao_reparaveis': non_repairable,
        'metodo': 'ST_MakeValid + ST_CollectionExtract + ST_Multi',
        'raw_preservada': True,
        'politica_operacional': 'somente_validas_ou_reparaveis',
        'pendencias_excluidas_operacional': non_repairable,
        'amostras_pendencias': samples,
        'amostras_limitadas_a': 50,
    }

def _rename_staging_primary_key(schema, table, raw_table):
    """Evita colisão de incoming_pkey ao mover várias RAWs para o mesmo schema.

    ogr2ogr cria a PK/index com base no nome temporário `incoming`. PostgreSQL
    exige nomes de índices únicos dentro do schema de destino. Renomeamos a
    constraint ainda no staging para um nome determinístico por dataset.
    """
    relation = f'{schema}.{table}'
    with connection.cursor() as c:
        c.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = to_regclass(%s) AND contype='p' LIMIT 1",
            [relation],
        )
        row = c.fetchone()
        if not row:
            return None
        old_name = row[0]
        new_name = f'{raw_table[:52]}_pkey'
        if old_name == new_name:
            return new_name
        c.execute(
            sql.SQL('ALTER TABLE {}.{} RENAME CONSTRAINT {} TO {}').format(
                sql.Identifier(schema), sql.Identifier(table),
                sql.Identifier(old_name), sql.Identifier(new_name),
            )
        )
        return new_name


def inspect_staging_table(schema, table):
    gm = _geometry_metadata(schema, table)
    with connection.cursor() as c:
        t = sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))
        c.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(t))
        total = c.fetchone()[0]
        if not gm:
            return {
                'registros': total,
                'geometry_column': None,
                'tipo_geometria': '',
                'srid': None,
                'geometrias_invalidas': 0,
                'geometrias_vazias': 0,
                'geometrias_nulas': 0,
            }
        g, gtype, srid = gm
        gi = sql.Identifier(g)
        c.execute(
            sql.SQL(
                'SELECT '
                'COUNT(*) FILTER (WHERE {g} IS NOT NULL AND NOT ST_IsValid({g})), '
                'COUNT(*) FILTER (WHERE {g} IS NOT NULL AND ST_IsEmpty({g})), '
                'COUNT(*) FILTER (WHERE {g} IS NULL) FROM {t}'
            ).format(g=gi, t=t)
        )
        invalid, empty, nulls = c.fetchone()
        return {
            'registros': total,
            'geometry_column': g,
            'tipo_geometria': gtype,
            'srid': srid,
            'geometrias_invalidas': invalid,
            'geometrias_vazias': empty,
            'geometrias_nulas': nulls,
        }


def table_exists(schema, table):
    with connection.cursor() as c:
        c.execute('SELECT to_regclass(%s)', [f'{schema}.{table}'])
        return c.fetchone()[0] is not None


def _ensure_schema(schema):
    with connection.cursor() as c:
        c.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))


def _move_table(source_schema, table, target_schema, new_name):
    with connection.cursor() as c:
        c.execute(
            sql.SQL('ALTER TABLE {}.{} SET SCHEMA {}').format(
                sql.Identifier(source_schema),
                sql.Identifier(table),
                sql.Identifier(target_schema),
            )
        )
        if table != new_name:
            c.execute(
                sql.SQL('ALTER TABLE {}.{} RENAME TO {}').format(
                    sql.Identifier(target_schema),
                    sql.Identifier(table),
                    sql.Identifier(new_name),
                )
            )


def _drop_table(schema, table):
    with connection.cursor() as c:
        c.execute(sql.SQL('DROP TABLE {}.{}').format(sql.Identifier(schema), sql.Identifier(table)))



def _drop_legacy_sicar_partition_raws(schema, raw_base):
    """Remove RAWs por UF criadas apenas pela estratégia v0.2.5.

    A tabela operacional já contém os dados promovidos; estas RAWs antigas são
    artefatos de uma partição administrativa que deixou de existir na v0.2.6.
    """
    removed = []
    prefix = f'{raw_base}_'
    with connection.cursor() as c:
        c.execute(
            'SELECT tablename FROM pg_tables WHERE schemaname=%s AND tablename LIKE %s ORDER BY tablename',
            [schema, prefix + '__'],
        )
        for (table_name,) in c.fetchall():
            suffix = table_name[len(prefix):].upper() if table_name.startswith(prefix) else ''
            if len(suffix) == 2 and suffix.isalpha():
                c.execute(
                    sql.SQL('DROP TABLE {}.{}').format(sql.Identifier(schema), sql.Identifier(table_name))
                )
                removed.append(table_name)
    return removed

def _promote_raw_only(importacao, layer, staging_schema, spec, schema_drift=None, raw_table_name=None):
    """Publica somente a RAW para perfis ainda sem modelo operacional validado.

    A tabela recebida substitui a RAW anterior dentro de transação. Nenhuma
    coluna canônica é inventada e nenhuma tabela operacional é construída.
    """
    schema = FONTE_SCHEMAS[spec.fonte]
    raw_table_name = raw_table_name or spec.raw_table
    now = timezone.now()
    stats = layer.get('db_stats') or {}
    with transaction.atomic():
        with connection.cursor() as lock_cursor:
            lock_cursor.execute(
                'SELECT pg_advisory_xact_lock(hashtext(%s))',
                [f'confronta:raw-only:{schema}:{raw_table_name}'],
            )
        _ensure_schema(schema)
        if table_exists(schema, raw_table_name):
            _drop_table(schema, raw_table_name)
        staging_table = raw_table_name if table_exists(staging_schema, raw_table_name) else 'incoming'
        raw_primary_key = None
        if staging_table == 'incoming':
            raw_primary_key = _rename_staging_primary_key(staging_schema, staging_table, raw_table_name)
        _move_table(staging_schema, staging_table, schema, raw_table_name)
        obj, created = CamadaImportada.objects.get_or_create(
            fonte=spec.fonte,
            dataset_slug=spec.slug,
            schema_banco=schema,
            nome_tabela=raw_table_name,
            defaults={
                'nome_original': layer.get('layer_name', ''),
                'tabela_raw': raw_table_name,
                'tipo_geometria': stats.get('tipo_geometria') or layer.get('geometry_type', ''),
                'srid': stats.get('srid') or layer.get('epsg_detectado'),
                'assinatura_estrutura': layer.get('signature', ''),
                'primeira_importacao': now,
                'ultima_importacao': now,
                'status': CamadaImportada.Status.ATIVA,
                'ultima_importacao_ref': importacao,
            },
        )
        if not created:
            obj.nome_original = layer.get('layer_name', '')
            obj.tabela_raw = raw_table_name
            obj.tipo_geometria = stats.get('tipo_geometria') or layer.get('geometry_type', '')
            obj.srid = stats.get('srid') or layer.get('epsg_detectado')
            obj.assinatura_estrutura = layer.get('signature', '')
            obj.ultima_importacao = now
            obj.status = CamadaImportada.Status.ATIVA
            obj.data_sem_uso = None
            obj.ultima_importacao_ref = importacao
            obj.save()
        drop_schema(staging_schema)
    return {
        'schema_destino': schema,
        'dataset': spec.slug,
        'tabela_raw': f'{schema}.{raw_table_name}',
        'tabela_operacional': None,
        'raw_only': True,
        'estrutura_alterada': bool(schema_drift and schema_drift.get('changed')),
        'alteracoes_estrutura': schema_drift or {},
        'constraint_raw': raw_primary_key,
        'normalizacao': {},
    }


def promote_dataset(importacao, layer, staging_schema, spec, schema_drift=None, raw_table_name=None, partition_context=None):
    if spec.mode == 'raw_only':
        return _promote_raw_only(importacao, layer, staging_schema, spec, schema_drift=schema_drift, raw_table_name=raw_table_name)
    schema = FONTE_SCHEMAS[spec.fonte]
    raw_table_name = raw_table_name or spec.raw_table
    partition_context = partition_context or {}
    now = timezone.now()
    legacy_sicar_raws_removed = []
    existing = CamadaImportada.objects.filter(
        fonte=spec.fonte,
        dataset_slug=spec.slug,
        nome_tabela=spec.stable_table,
    ).first()

    # Se o pipeline trouxe comparação detalhada, ela é a fonte de verdade.
    # Mantemos o fallback por assinatura para instalações antigas sem snapshot.
    if schema_drift is not None:
        altered = bool(schema_drift.get('changed'))
    else:
        altered = bool(existing and existing.assinatura_estrutura != layer['signature'])

    try:
        with transaction.atomic():
            # Serializa promoções que escrevem na mesma tabela operacional. Isso
            # protege lote x manual e também múltiplos workers no futuro.
            with connection.cursor() as lock_cursor:
                lock_cursor.execute(
                    'SELECT pg_advisory_xact_lock(hashtext(%s))',
                    [f'confronta:{schema}:{spec.stable_table}'],
                )
            _ensure_schema(schema)
            if spec.fonte_slug == 'sicar':
                legacy_sicar_raws_removed = _drop_legacy_sicar_partition_raws(schema, spec.raw_table)
            # RAW é substituída dentro da transação. Se a normalização falhar, o rollback
            # restaura a versão anterior; não usamos CASCADE nem apagamos a tabela operacional.
            if table_exists(schema, raw_table_name):
                _drop_table(schema, raw_table_name)
            # Desde v0.2.4 o ogr2ogr cria a tabela temporária já com o nome RAW
            # definitivo. Assim PostgreSQL gera uma PK/index específica por dataset
            # (raw_xxx_pkey), evitando a colisão global de `incoming_pkey`.
            staging_table = raw_table_name if table_exists(staging_schema, raw_table_name) else 'incoming'
            raw_primary_key = f'{raw_table_name[:52]}_pkey' if staging_table == raw_table_name else None
            if staging_table == 'incoming':
                # Compatibilidade defensiva com chamadas antigas/internas.
                raw_primary_key = _rename_staging_primary_key(staging_schema, staging_table, raw_table_name)
            _move_table(staging_schema, staging_table, schema, raw_table_name)
            normalization = build_normalized_table(
                schema,
                raw_table_name,
                spec.stable_table,
                spec,
                append_partition=(spec.mode == 'replace_partition'),
                partition_context=(partition_context if (spec.mode == 'replace_partition' or spec.fonte_slug == 'sicar') else None),
                merge_by_key=('cod_imovel' if spec.fonte_slug == 'sicar' else None),
            )
            stats = layer['db_stats']
            obj, created = CamadaImportada.objects.get_or_create(
                fonte=spec.fonte,
                dataset_slug=spec.slug,
                schema_banco=schema,
                nome_tabela=spec.stable_table,
                defaults={
                    'nome_original': layer['layer_name'],
                    'tabela_raw': raw_table_name,
                    'tipo_geometria': stats.get('tipo_geometria') or layer.get('geometry_type', ''),
                    'srid': stats.get('srid') or layer.get('epsg_detectado'),
                    'assinatura_estrutura': layer['signature'],
                    'primeira_importacao': now,
                    'ultima_importacao': now,
                    'status': CamadaImportada.Status.ATIVA,
                    'ultima_importacao_ref': importacao,
                },
            )
            if not created:
                obj.nome_original = layer['layer_name']
                obj.tabela_raw = raw_table_name
                obj.tipo_geometria = stats.get('tipo_geometria') or layer.get('geometry_type', '')
                obj.srid = stats.get('srid') or layer.get('epsg_detectado')
                obj.assinatura_estrutura = layer['signature']
                obj.ultima_importacao = now
                obj.status = CamadaImportada.Status.ATIVA
                obj.data_sem_uso = None
                obj.ultima_importacao_ref = importacao
                obj.save()

            Alerta.objects.filter(
                camada=obj,
                tipo=Alerta.Tipo.TABELA_NAO_UTILIZADA,
                ativo=True,
            ).update(ativo=False, resolvido_em=now)

            if altered:
                message = format_alert_message(spec, schema_drift) if schema_drift else ''
                if not message:
                    message = (
                        f'A estrutura recebida para {spec.label} mudou em relação à importação anterior. '
                        f'Revise o relatório da importação #{importacao.pk}.'
                    )
                Alerta.objects.create(
                    tipo=Alerta.Tipo.ALTERACAO_ESTRUTURAL,
                    fonte=spec.fonte,
                    camada=obj,
                    mensagem=message,
                )
            drop_schema(staging_schema)

        return {
            'schema_destino': schema,
            'dataset': spec.slug,
            'tabela_raw': f'{schema}.{raw_table_name}',
            'tabela_operacional': f'{schema}.{spec.stable_table}',
            'estrutura_alterada': altered,
            'alteracoes_estrutura': schema_drift or {},
            'adaptacoes_ogr2ogr': layer.get('ogr2ogr_adaptacoes', {}),
            'constraint_raw': raw_primary_key,
            'geometrias': layer.get('geometry_repair', {}),
            'raws_sicar_legadas_removidas': legacy_sicar_raws_removed,
            'normalizacao': normalization,
        }
    except Exception as exc:
        raise PromotionError(f'Falha durante a promoção atômica do dataset {spec.slug}: {exc}') from exc


def delete_unused_table(camada):
    if camada.status != CamadaImportada.Status.NAO_ENCONTRADA:
        raise PromotionError('Somente tabelas marcadas como não encontradas podem ser excluídas por este fluxo.')
    if table_exists(camada.schema_banco, camada.nome_tabela):
        with transaction.atomic():
            _drop_table(camada.schema_banco, camada.nome_tabela)
            camada.status = CamadaImportada.Status.REMOVIDA
            camada.save(update_fields=['status'])
    else:
        camada.status = CamadaImportada.Status.REMOVIDA
        camada.save(update_fields=['status'])
