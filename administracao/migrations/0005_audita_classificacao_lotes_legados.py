import re
import unicodedata
from django.db import migrations


def _norm(value):
    value = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def _expected_slug(source, filename):
    name = _norm(filename)
    if name.endswith('_zip'):
        name = name[:-4]

    if source == 'SICAR':
        rules = (
            ('area_consolidada', 'sicar-area-consolidada'),
            ('area_imovel', 'sicar-perimetros'),
            ('area_pousio', 'sicar-area-pousio'),
            ('servidao_administrativa', 'sicar-servidao-administrativa'),
            ('vegetacao_nativa', 'sicar-vegetacao-nativa'),
            ('reserva_legal', 'sicar-reserva-legal'),
            ('uso_restrito', 'sicar-uso-restrito'),
            ('hidrografia', 'sicar-hidrografia'),
            ('apps', 'sicar-app'),
        )
        for token, slug in rules:
            if name == token or name.startswith(token + '_') or ('_' + token + '_') in ('_' + name + '_'):
                return slug

    if source == 'INCRA':
        if 'quilombol' in name:
            return 'incra-quilombolas'
        if 'assentamento' in name:
            return 'incra-assentamentos'

    if source == 'PRODES':
        if 'cerrado' in name:
            return 'prodes-cerrado-supressao'
        if 'mata_atlantica' in name or 'mataatlantica' in name:
            return 'prodes-mata-atlantica-supressao'
        if 'caatinga' in name:
            return 'prodes-caatinga-supressao'
        if 'pampa' in name:
            return 'prodes-pampa-supressao'
        if 'pantanal' in name:
            return 'prodes-pantanal-supressao'
        if 'amazon' in name or 'amazonia' in name:
            if 'non_forest' in name or 'nonforest' in name or 'nao_florestal' in name:
                return 'prodes-amazonia-nao-florestal'
            return 'prodes-amazonia-desmatamento'
    return None


def audit_legacy_batches(apps, schema_editor):
    Item = apps.get_model('administracao', 'ItemLoteImportacao')
    Lote = apps.get_model('administracao', 'LoteImportacao')
    Importacao = apps.get_model('administracao', 'Importacao')

    affected_lotes = {}
    qs = Item.objects.select_related('lote').exclude(dataset_slug='')
    for item in qs.iterator():
        expected = _expected_slug(item.lote.fonte, item.nome_arquivo)
        if not expected or expected == item.dataset_slug:
            continue
        if item.status not in ('CONCLUIDO', 'IGNORADO_DUPLICADO'):
            continue

        previous = item.dataset_slug
        item.status = 'REQUER_REVISAO'
        item.motivo = (
            'Auditoria automática da v0.2.7: o nome oficial do arquivo indica '
            f'{expected}, mas o lote legado o classificou como {previous}. '
            'A classificação antiga não será mais usada como histórico confiável. '
            'Reenvie o arquivo/lote para reconstruir o destino correto.'
        )
        item.save(update_fields=['status', 'motivo'])

        if item.importacao_id:
            Importacao.objects.filter(pk=item.importacao_id).update(
                identidade_status='CLASSIFICACAO_INCONSISTENTE'
            )
        affected_lotes[item.lote_id] = affected_lotes.get(item.lote_id, 0) + 1

    for lote_id, count in affected_lotes.items():
        lote = Lote.objects.get(pk=lote_id)
        result = dict(lote.resultado or {})
        # Remove o resumo calculado pelo classificador legado para não exibir
        # contagens de datasets que acabaram de ser marcadas como inconsistentes.
        result.pop('sicar_datasets', None)
        result['auditoria_classificacao_v2'] = {
            'itens_inconsistentes_detectados': count,
            'acao': 'REENVIAR_LOTE',
        }
        lote.resultado = result
        if lote.status in ('CONCLUIDO', 'CONCLUIDO_COM_PENDENCIAS'):
            lote.status = 'CONCLUIDO_COM_PENDENCIAS'
        lote.save(update_fields=['resultado', 'status'])


class Migration(migrations.Migration):
    dependencies = [
        ('administracao', '0004_lotes_contexto_importacao'),
    ]

    operations = [
        migrations.RunPython(audit_legacy_batches, migrations.RunPython.noop),
    ]
