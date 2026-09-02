from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from administracao.forms import ConfirmarExclusaoTabelaForm
from administracao.models import Alerta, CamadaImportada
from administracao.permissions import admin_required, table_manager_required
from administracao.services.auditoria import registrar_auditoria
from administracao.services.postgis import delete_unused_table


@admin_required
def alertas(request):
    ativos = Alerta.objects.filter(ativo=True).select_related('camada').order_by('-criado_em')
    camadas = CamadaImportada.objects.exclude(status=CamadaImportada.Status.REMOVIDA).order_by('fonte', 'nome_tabela')
    return render(request, 'administracao/alertas/lista.html', {'alertas': ativos, 'camadas': camadas})


@table_manager_required
def excluir_tabela(request, pk):
    camada = get_object_or_404(CamadaImportada, pk=pk)
    if camada.status != CamadaImportada.Status.NAO_ENCONTRADA:
        messages.error(request, 'Apenas tabelas marcadas como não encontradas podem ser excluídas.')
        return redirect('administracao:alertas')
    form = ConfirmarExclusaoTabelaForm(request.POST or None, expected_table=camada.nome_tabela)
    if request.method == 'POST' and form.is_valid():
        try:
            delete_unused_table(camada)
        except Exception as exc:
            registrar_auditoria(
                request.user, 'EXCLUSAO_TABELA_BLOQUEADA', 'CamadaImportada', camada.pk,
                {'fonte': camada.fonte, 'dataset': camada.dataset_slug, 'schema': camada.schema_banco, 'tabela': camada.nome_tabela, 'motivo': str(exc)},
            )
            messages.error(
                request,
                'A tabela não foi excluída. O PostgreSQL recusou a operação, possivelmente por dependências. '
                f'Detalhe técnico: {exc}'
            )
            return redirect('administracao:alertas')
        now = timezone.now()
        Alerta.objects.filter(camada=camada, ativo=True).update(ativo=False, resolvido_em=now, resolvido_por=request.user)
        registrar_auditoria(
            request.user, 'TABELA_EXCLUIDA', 'CamadaImportada', camada.pk,
            {'fonte': camada.fonte, 'dataset': camada.dataset_slug, 'schema': camada.schema_banco, 'tabela': camada.nome_tabela},
        )
        messages.success(request, f'Tabela {camada.schema_banco}.{camada.nome_tabela} excluída e auditada.')
        return redirect('administracao:alertas')
    return render(request, 'administracao/alertas/excluir.html', {'camada': camada, 'form': form})
