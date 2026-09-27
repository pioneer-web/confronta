import logging

from django.db import connection
from django.db import transaction
from django.utils import timezone
from psycopg import sql

from administracao.services.postgis import table_exists

logger = logging.getLogger(__name__)


def count_rows(schema, table, year=None, valid_geometries=False):
    if not table_exists(schema, table):
        return 0
    query = sql.SQL('SELECT COUNT(*) FROM {}').format(
        sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))
    )
    params = []
    clauses = []
    if year is not None:
        clauses.append(sql.SQL('_ano_arquivo = %s'))
        params.append(year)
    if valid_geometries:
        clauses.append(sql.SQL('geom IS NOT NULL AND NOT ST_IsEmpty(geom) AND ST_IsValid(geom)'))
    if clauses:
        query += sql.SQL(' WHERE ') + sql.SQL(' AND ').join(clauses)
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return int(cursor.fetchone()[0] or 0)


def count_staging_rows(schema, table, year=None, valid_geometries=False):
    query = sql.SQL('SELECT COUNT(*) FROM {}').format(
        sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))
    )
    clauses = []
    params = []
    if year is not None:
        clauses.append(sql.SQL('_ano_arquivo = %s'))
        params.append(year)
    if valid_geometries:
        clauses.append(sql.SQL('geom IS NOT NULL AND NOT ST_IsEmpty(geom) AND ST_IsValid(geom)'))
    if clauses:
        query += sql.SQL(' WHERE ') + sql.SQL(' AND ').join(clauses)
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return int(cursor.fetchone()[0] or 0)


def make_staging_durable(schema, tables):
    """Persist prepared relations before waiting for an administrator decision."""
    with connection.cursor() as cursor:
        for table in tables:
            if table:
                cursor.execute(sql.SQL('ALTER TABLE {} SET LOGGED').format(
                    sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))
                ))


def comparison(base_count, new_count, *, year=None, policy, preserve_years=False, snapshot=False):
    difference = int(new_count) - int(base_count)
    percent = (difference / int(base_count) * 100) if base_count else None
    result = {
        'ano_referencia': year,
        'registros_base_atual': int(base_count),
        'registros_nova_versao': int(new_count),
        'diferenca_registros': difference,
        'diferenca_percentual': round(percent, 2) if percent is not None else None,
        'politica': policy,
        'anos_anteriores_preservados': bool(preserve_years) if not snapshot else None,
        'snapshot_completo': bool(snapshot),
        'reducao_detectada': bool(base_count and new_count < base_count),
    }
    if snapshot:
        result.update({
            'snapshot_atual_glebas': int(base_count),
            'novo_snapshot_glebas': int(new_count),
            'mensagem': 'Este dataset é um snapshot completo. A nova versão substituirá o snapshot atualmente publicado.',
        })
    return result


def resume_pending_sicor_import(importacao_id, usuario):
    from administracao.datasets import get_dataset
    from administracao.models import Importacao

    imp = Importacao.objects.get(pk=importacao_id)
    spec = get_dataset(imp.dataset_slug)
    if spec and spec.data_kind == 'sicor_operacoes':
        from .sicor_operations_import import resume_sicor_operations_import
        return resume_sicor_operations_import(importacao_id, usuario)
    if spec and spec.data_kind in {'sicor_wkt', 'sicor_gleba_points'}:
        from .sicor_import import resume_sicor_tabular_import
        return resume_sicor_tabular_import(importacao_id, usuario)
    raise ValueError('Esta importação não possui uma publicação SICOR aguardando confirmação.')


def cancel_pending_sicor_import(importacao_id, usuario):
    from administracao.models import Importacao
    from .auditoria import registrar_auditoria
    from .postgis import drop_schema

    with transaction.atomic():
        imp = Importacao.objects.select_for_update().get(pk=importacao_id)
        if imp.status != Importacao.Status.AGUARDANDO_CONFIRMACAO_REDUCAO:
            raise ValueError('Esta publicação SICOR não está aguardando confirmação.')
        result = dict(imp.resultado or {})
        pending = result.get('pending_sicor_publication') or {}
        operations = (result.get('sicor_operacoes') or {})
        staging = pending.get('staging_schema') or operations.get('staging_schema')
        imp.status = Importacao.Status.INTERROMPIDO
        imp.data_finalizacao = timezone.now()
        imp.motivo_rejeicao = 'Substituição cancelada pelo administrador; a versão ativa foi preservada.'
        result['publicacao_cancelada'] = True
        result['base_ativa_preservada'] = True
        imp.resultado = result
        imp.save(update_fields=['status', 'data_finalizacao', 'motivo_rejeicao', 'resultado'])
        registrar_auditoria(usuario, 'SICOR_REDUCAO_SUBSTITUICAO_CANCELADA', 'Importacao', imp.pk, {
            'dataset': imp.dataset_slug,
            **((result.get('comparacao_publicacao') or operations.get('comparacao_publicacao') or {})),
            'confirmacao_manual': False,
        })
    if staging:
        try:
            drop_schema(staging)
        except Exception:
            logger.exception('Falha ao remover staging SICOR cancelado da importação %s.', imp.pk)
    return imp
