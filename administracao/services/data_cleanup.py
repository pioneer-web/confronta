from django.db import connection, transaction
from django.utils import timezone
from psycopg import sql

from administracao.constants import FONTE_SCHEMAS
from administracao.models import CamadaImportada, SicarEstado, SicarFingerprintCamada
from .auditoria import registrar_auditoria
from .postgis import table_exists


def _relation(schema, table):
    return sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))


def _count_rows(schema, table, filters=()):
    if not table_exists(schema, table):
        return 0
    query = sql.SQL('SELECT COUNT(*) FROM {}').format(_relation(schema, table))
    params = []
    if filters:
        conditions = []
        for field, value in filters:
            conditions.append(sql.SQL('{} = %s').format(sql.Identifier(field)))
            params.append(value)
        query += sql.SQL(' WHERE ') + sql.SQL(' AND ').join(conditions)
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return int(cursor.fetchone()[0] or 0)


def dataset_storage_status(spec):
    """Retorna o estado atual do dataset sem alterar o banco."""
    schema = FONTE_SCHEMAS[spec.fonte]
    partition_filters = tuple(spec.fixed_values or ()) if spec.mode == 'replace_partition' else ()
    raw_only = spec.mode == 'raw_only'
    operational_exists = False if raw_only else table_exists(schema, spec.stable_table)
    raw_exists = table_exists(schema, spec.raw_table)
    return {
        'schema': schema,
        'operational_table': None if raw_only else spec.stable_table,
        'raw_table': spec.raw_table,
        'operational_exists': operational_exists,
        'raw_exists': raw_exists,
        'operational_rows': 0 if raw_only else _count_rows(schema, spec.stable_table, partition_filters),
        'raw_rows': _count_rows(schema, spec.raw_table),
        'partitioned': bool(partition_filters),
        'partition_filters': partition_filters,
        'raw_only': raw_only,
    }


def _clear_operational(spec, schema):
    if not table_exists(schema, spec.stable_table):
        return 0, 'ausente'

    filters = tuple(spec.fixed_values or ()) if spec.mode == 'replace_partition' else ()
    before = _count_rows(schema, spec.stable_table, filters)
    with connection.cursor() as cursor:
        if filters:
            conditions = []
            params = []
            for field, value in filters:
                conditions.append(sql.SQL('{} = %s').format(sql.Identifier(field)))
                params.append(value)
            cursor.execute(
                sql.SQL('DELETE FROM {} WHERE ').format(_relation(schema, spec.stable_table))
                + sql.SQL(' AND ').join(conditions),
                params,
            )
            method = 'DELETE_PARTITION'
        else:
            # Sem CASCADE: se surgir dependência relacional inesperada, PostgreSQL
            # bloqueará a operação e toda a transação será revertida.
            cursor.execute(sql.SQL('TRUNCATE TABLE {}').format(_relation(schema, spec.stable_table)))
            method = 'TRUNCATE'
    return before, method


def _clear_raw(spec, schema):
    if not table_exists(schema, spec.raw_table):
        return 0, 'ausente'
    before = _count_rows(schema, spec.raw_table)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL('TRUNCATE TABLE {}').format(_relation(schema, spec.raw_table)))
    return before, 'TRUNCATE'



def _assert_no_active_imports(source_slug, dataset_slug=None):
    from administracao.constants import FONTE_SLUGS
    from administracao.models import Importacao, LoteImportacao

    fonte = FONTE_SLUGS.get(str(source_slug or '').strip().lower())
    if not fonte:
        return
    active_lote_statuses = [
        LoteImportacao.Status.PREPARANDO,
        LoteImportacao.Status.ANALISANDO,
        LoteImportacao.Status.AGUARDANDO_CONFIRMACAO,
        LoteImportacao.Status.PROCESSANDO,
    ]
    if LoteImportacao.objects.filter(fonte=fonte, status__in=active_lote_statuses).exists():
        raise ValueError('Existe um lote ativo desta fonte. Finalize ou interrompa o lote antes de excluir os dados atuais.')

    active_import_statuses = [
        Importacao.Status.RECEBIDO,
        Importacao.Status.VALIDANDO,
        Importacao.Status.VALIDANDO_IDENTIDADE,
        Importacao.Status.VALIDANDO_GIS,
        Importacao.Status.IMPORTANDO,
    ]
    qs = Importacao.objects.filter(fonte=fonte, status__in=active_import_statuses)
    if dataset_slug:
        qs = qs.filter(dataset_slug=dataset_slug)
    if qs.exists():
        raise ValueError('Existe uma importação ativa para estes dados. Aguarde a conclusão antes de executar a exclusão.')

def clear_dataset_data(spec, usuario):
    """Esvazia os dados de um perfil mantendo tabelas, estrutura e histórico.

    A operação é intencionalmente conservadora:
    - nunca usa DROP TABLE/DROP SCHEMA;
    - nunca usa CASCADE;
    - PRODES limpa somente a partição lógica do perfil na tabela consolidada;
    - mantém Importacao/Auditoria como histórico;
    - invalida fingerprints SICAR para permitir uma nova carga integral segura.
    """
    _assert_no_active_imports(spec.fonte_slug, dataset_slug=spec.slug)
    schema = FONTE_SCHEMAS[spec.fonte]
    now = timezone.now()

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT pg_advisory_xact_lock(hashtext(%s))',
                [f'confronta:clear:{schema}:{spec.stable_table}:{spec.slug}'],
            )

        if spec.mode == 'raw_only':
            operational_rows, operational_method = 0, 'NAO_APLICAVEL_RAW_ONLY'
            raw_rows, raw_method = _clear_raw(spec, schema)
        else:
            operational_rows, operational_method = _clear_operational(spec, schema)
            raw_rows, raw_method = _clear_raw(spec, schema)

        camada = CamadaImportada.objects.filter(
            fonte=spec.fonte,
            dataset_slug=spec.slug,
            schema_banco=schema,
            nome_tabela=spec.stable_table,
        ).first()
        if camada:
            camada.status = CamadaImportada.Status.PENDENTE_REVISAO
            camada.data_sem_uso = now
            camada.save(update_fields=['status', 'data_sem_uso'])

        fingerprints_removed = 0
        states_marked = 0
        if spec.fonte_slug == 'sicar':
            fingerprints_removed, _ = SicarFingerprintCamada.objects.filter(dataset_slug=spec.slug).delete()
            states = list(SicarEstado.objects.all())
            for state in states:
                details = dict(state.detalhes or {})
                details['limpeza_manual'] = {
                    'dataset_slug': spec.slug,
                    'data': now.isoformat(),
                }
                state.status = SicarEstado.Status.ATENCAO
                state.detalhes = details
                state.save(update_fields=['status', 'detalhes', 'atualizado_em'])
            states_marked = len(states)

        result = {
            'dataset': spec.slug,
            'label': spec.label,
            'schema': schema,
            'tabela_operacional': spec.stable_table,
            'tabela_raw': spec.raw_table,
            'registros_operacionais_removidos': operational_rows,
            'registros_raw_removidos': raw_rows,
            'metodo_operacional': operational_method,
            'metodo_raw': raw_method,
            'particao_logica': list(spec.fixed_values or ()) if spec.mode == 'replace_partition' else [],
            'fingerprints_sicar_removidos': fingerprints_removed,
            'estados_sicar_marcados_atencao': states_marked,
            'data': now.isoformat(),
        }
        registrar_auditoria(
            usuario,
            'DATASET_DADOS_LIMPOS_MANUALMENTE',
            'Dataset',
            spec.slug,
            result,
        )
        return result


def clear_source_data(source_slug, usuario):
    """Esvazia todas as tabelas operacionais e RAW de uma fonte implementada.

    A exclusão é uma ação administrativa separada da importação:
    - mantém schemas, tabelas, índices, migrations e histórico;
    - nunca usa DROP nem CASCADE;
    - executa todas as limpezas da fonte em uma única transação;
    - marca as camadas como sem uso para que históricos de hash/fingerprint
      não impeçam a reconstrução posterior de uma base explicitamente vazia.
    """
    from administracao.constants import FONTE_SLUGS
    from administracao.datasets import datasets_for_source

    source_slug = str(source_slug or '').strip().lower()
    fonte = FONTE_SLUGS.get(source_slug)
    specs = datasets_for_source(source_slug)
    if not fonte or not specs:
        raise ValueError('A fonte não possui tabelas técnicas implementadas para exclusão.')

    _assert_no_active_imports(source_slug)
    schema = FONTE_SCHEMAS[fonte]
    stable_tables = sorted({spec.stable_table for spec in specs if spec.mode != 'raw_only'})
    raw_tables = sorted({spec.raw_table for spec in specs})
    now = timezone.now()

    result = {
        'fonte_slug': source_slug,
        'fonte': str(fonte),
        'schema': schema,
        'tabelas_operacionais': [],
        'tabelas_raw': [],
        'tabelas_ausentes': [],
        'fingerprints_sicar_removidos': 0,
        'estados_sicar_marcados_atencao': 0,
        'data': now.isoformat(),
    }

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT pg_advisory_xact_lock(hashtext(%s))',
                [f'confronta:clear-source:{schema}:{source_slug}'],
            )

            for table in stable_tables:
                if table_exists(schema, table):
                    cursor.execute(sql.SQL('TRUNCATE TABLE {}').format(_relation(schema, table)))
                    result['tabelas_operacionais'].append(table)
                else:
                    result['tabelas_ausentes'].append(table)

            for table in raw_tables:
                if table_exists(schema, table):
                    cursor.execute(sql.SQL('TRUNCATE TABLE {}').format(_relation(schema, table)))
                    result['tabelas_raw'].append(table)
                else:
                    result['tabelas_ausentes'].append(table)

        CamadaImportada.objects.filter(
            fonte=fonte,
            schema_banco=schema,
            dataset_slug__in=[spec.slug for spec in specs],
        ).update(status=CamadaImportada.Status.PENDENTE_REVISAO, data_sem_uso=now)

        if source_slug == 'sicar':
            removed, _ = SicarFingerprintCamada.objects.all().delete()
            result['fingerprints_sicar_removidos'] = removed
            states = list(SicarEstado.objects.all())
            for state in states:
                details = dict(state.detalhes or {})
                details['exclusao_manual_fonte'] = {
                    'fonte': source_slug,
                    'data': now.isoformat(),
                }
                state.status = SicarEstado.Status.ATENCAO
                state.detalhes = details
                state.save(update_fields=['status', 'detalhes', 'atualizado_em'])
            result['estados_sicar_marcados_atencao'] = len(states)

        registrar_auditoria(
            usuario,
            'FONTE_DADOS_EXCLUIDOS_MANUALMENTE',
            'FonteDados',
            source_slug,
            result,
        )

    return result
