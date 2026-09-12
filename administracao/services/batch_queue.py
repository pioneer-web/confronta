from django.db import transaction
from django.utils import timezone

from administracao.models import ItemLoteImportacao, LoteImportacao


def claim_next_item():
    with transaction.atomic():
        item = (
            ItemLoteImportacao.objects.select_for_update(skip_locked=True)
            .select_related('lote','lote__administrador')
            .filter(
                status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
                lote__status__in=[LoteImportacao.Status.ANALISANDO, LoteImportacao.Status.PROCESSANDO],
            )
            .order_by('id')
            .first()
        )
        if not item:
            return None
        item.status = ItemLoteImportacao.Status.PROCESSANDO
        item.iniciado_em = timezone.now()
        item.progresso = 3
        item.etapa = 'Iniciando análise'
        item.save(update_fields=['status','iniciado_em','progresso','etapa'])
        return item


def _set_item_progress(item_id, percent, stage):
    percent = max(0, min(100, int(percent)))
    ItemLoteImportacao.objects.filter(pk=item_id).update(progresso=percent, etapa=str(stage)[:160])


def _finish_item(item, status, stage, motivo='', *, importacao=None, progress=100):
    item.status = status
    item.etapa = stage
    item.motivo = motivo
    item.progresso = max(0, min(100, int(progress)))
    item.finalizado_em = timezone.now()
    if importacao is not None:
        item.importacao = importacao
    fields = ['status', 'etapa', 'motivo', 'progresso', 'finalizado_em']
    if importacao is not None:
        fields.append('importacao')
    item.save(update_fields=fields)
    return item
