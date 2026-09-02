from django.shortcuts import render

from administracao.models import Importacao, LoteImportacao, SicarEstado
from administracao.permissions import admin_required
from administracao.source_catalog import catalog_summary


@admin_required
def dashboard(request):
    context = {
        'lotes_recentes': LoteImportacao.objects.select_related('administrador').filter(oculto_painel=False)[:12],
        'importacoes_recentes': Importacao.objects.select_related('administrador').all()[:12],
        'aguardando_confirmacao': LoteImportacao.objects.filter(oculto_painel=False, status=LoteImportacao.Status.AGUARDANDO_CONFIRMACAO).count(),
        'em_processamento': LoteImportacao.objects.filter(oculto_painel=False, status__in=[LoteImportacao.Status.ANALISANDO, LoteImportacao.Status.PROCESSANDO, LoteImportacao.Status.INTERROMPENDO]).count(),
        'ufs_monitoradas': SicarEstado.objects.exclude(status=SicarEstado.Status.NUNCA_IMPORTADO).count(),
        'catalog_summary': catalog_summary(),
    }
    return render(request, 'administracao/dashboard.html', context)
