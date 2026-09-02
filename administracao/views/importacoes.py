import json
import queue
import threading

from django.contrib import messages
from django.db import close_old_connections
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from administracao.constants import FONTE_SLUGS, BATCH_FONTE_SLUGS
from administracao.datasets import get_dataset, source_groups, datasets_for_source
from administracao.forms import ImportacaoLoteForm, UploadBaseForm, DefinirUfItemLoteForm
from administracao.models import Importacao, LoteImportacao, ItemLoteImportacao, SicarEstado, FonteSincronizacao
from administracao.permissions import admin_required, superadmin_required
from administracao.services.batch import (
    append_sequential_upload, allowed_input_extensions, calculate_batch_progress,
    confirm_batch_changes, create_batch, create_batch_from_uploads, create_sequential_batch,
    finalize_sequential_batch, retry_failed_batch_items, retry_review_batch_items,
    update_batch_status, request_batch_interruption, delete_batch_record,
)
from administracao.services.pipeline import process_import
from administracao.services.sicar_tracking import state_rows
from administracao.services.partitioning import normalize_uf, UF_NAMES
from administracao.services.source_sync import enqueue_ibama


@admin_required
def fonte_datasets(request, fonte_slug):
    fonte = FONTE_SLUGS.get(fonte_slug)
    if not fonte:
        return redirect('administracao:dashboard')
    if fonte_slug == 'sicar':
        rows = state_rows()
        summary = {
            'total': len(rows),
            'atualizados': sum(1 for row in rows if row['ultima_verificacao']),
            'nunca': sum(1 for row in rows if row['status'] == SicarEstado.Status.NUNCA_IMPORTADO),
            'atencao': sum(1 for row in rows if row['status'] in {SicarEstado.Status.ATENCAO, SicarEstado.Status.FALHOU}),
        }
        return render(
            request,
            'administracao/importacoes/sicar.html',
            {'fonte': fonte, 'fonte_slug': fonte_slug, 'estados': rows, 'resumo': summary},
        )
    return render(
        request,
        'administracao/importacoes/fonte.html',
        {
            'fonte': fonte,
            'fonte_slug': fonte_slug,
            'grupos': source_groups(fonte_slug),
            'batch_enabled': fonte_slug in BATCH_FONTE_SLUGS,
        },
    )


def _importacao_resultado_ui(imp):
    sucesso = imp.status in {
        Importacao.Status.CONCLUIDO,
        Importacao.Status.IGNORADO_DUPLICADO,
        Importacao.Status.SEM_ALTERACAO,
    }
    if imp.status == Importacao.Status.CONCLUIDO:
        if (imp.resultado or {}).get('raw_flexivel'):
            mensagem = 'Importação concluída. A estrutura oficial foi validada e preservada na RAW; o perfil operacional permanece pendente de validação.'
        else:
            mensagem = 'Importação concluída. O dataset foi identificado, validado e promovido.'
    elif imp.status == Importacao.Status.IGNORADO_DUPLICADO:
        mensagem = 'O arquivo é idêntico à versão já importada deste dataset. Nenhum reprocessamento foi necessário.'
    elif imp.status == Importacao.Status.SEM_ALTERACAO:
        mensagem = 'A validação foi concluída e nenhuma alteração precisou ser aplicada ao banco.'
    elif imp.status == Importacao.Status.REJEITADO_SEGURANCA:
        mensagem = 'O arquivo foi bloqueado por uma validação de segurança.'
    elif imp.status == Importacao.Status.REJEITADO_IDENTIDADE:
        mensagem = 'O conteúdo do arquivo não pôde ser confirmado como o dataset selecionado. Nada foi importado.'
    else:
        mensagem = imp.motivo_rejeicao or 'A importação não foi concluída. Consulte o relatório.'
    return sucesso, mensagem


def _stream_importacao(uploaded_file, spec, usuario, context=None):
    eventos = queue.Queue()
    fim = object()

    def publicar(percentual, etapa):
        eventos.put({
            'type': 'progress',
            'percent': max(0, min(100, int(percentual))),
            'stage': str(etapa or ''),
        })

    def executar():
        close_old_connections()
        try:
            imp = process_import(
                uploaded_file, spec.slug, usuario, context=dict(context or {}),
                progress_callback=publicar,
            )
            sucesso, mensagem = _importacao_resultado_ui(imp)
            eventos.put({
                'type': 'complete',
                'ok': sucesso,
                'status': imp.status,
                'status_label': imp.get_status_display(),
                'message': mensagem,
                'report_url': reverse('administracao:importacao_detalhe', args=[imp.pk]),
            })
        except Exception:
            eventos.put({
                'type': 'error',
                'message': 'O processamento foi interrompido por uma falha inesperada. Consulte os logs do servidor antes de reenviar o arquivo.',
            })
        finally:
            close_old_connections()
            eventos.put(fim)

    def stream():
        worker = threading.Thread(target=executar, name=f'confronta-import-{spec.slug}', daemon=True)
        worker.start()
        while True:
            evento = eventos.get()
            if evento is fim:
                break
            yield json.dumps(evento, ensure_ascii=False) + '\n'

    response = StreamingHttpResponse(stream(), content_type='application/x-ndjson; charset=utf-8')
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['X-Accel-Buffering'] = 'no'
    return response


@admin_required
def importar_dataset(request, fonte_slug, dataset_slug):
    spec = get_dataset(dataset_slug)
    if not spec or spec.fonte_slug != fonte_slug:
        return redirect('administracao:dashboard')
    form = UploadBaseForm(request.POST or None, request.FILES or None, source_slug=fonte_slug, dataset_slug=dataset_slug)
    progress_request = request.headers.get('X-Confronta-Progress') == 'stream'
    if request.method == 'POST':
        if form.is_valid():
            import_context = {}
            if fonte_slug == 'prodes':
                import_context['prodes_ano_inicial'] = form.cleaned_data.get('ano_inicial')
            if progress_request:
                # O pipeline já possui callbacks reais de progresso. Neste modo a
                # resposta é transmitida em NDJSON para a tela acompanhar cada etapa
                # sem alterar a lógica GIS, RAW ou promoção PostGIS existente.
                return _stream_importacao(
                    form.cleaned_data['arquivo'], spec, request.user, context=import_context
                )

            imp = process_import(
                form.cleaned_data['arquivo'], spec.slug, request.user, context=import_context
            )
            sucesso, mensagem = _importacao_resultado_ui(imp)
            if sucesso:
                messages.success(request, mensagem)
            else:
                messages.error(request, mensagem)
            return redirect('administracao:importacao_detalhe', pk=imp.pk)

        if progress_request:
            erros = []
            for field_errors in form.errors.values():
                erros.extend(str(error) for error in field_errors)
            payload = {
                'type': 'validation_error',
                'message': ' '.join(erros) or 'Verifique o arquivo selecionado e tente novamente.',
            }
            response = StreamingHttpResponse(
                iter([json.dumps(payload, ensure_ascii=False) + '\n']),
                status=400,
                content_type='application/x-ndjson; charset=utf-8',
            )
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response['X-Accel-Buffering'] = 'no'
            return response

    hist = Importacao.objects.filter(dataset_slug=spec.slug).select_related('administrador')[:10]
    return render(
        request,
        'administracao/importacoes/upload.html',
        {'form': form, 'spec': spec, 'historico': hist},
    )



@admin_required
def iniciar_lote_sequencial(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método não permitido.'}, status=405)
    source_slug = str(request.POST.get('fonte') or '').strip().lower()
    if source_slug not in BATCH_FONTE_SLUGS:
        return JsonResponse({'ok': False, 'error': 'Esta fonte não usa o lote GIS genérico.'}, status=400)
    try:
        expected = int(request.POST.get('total_arquivos') or 0)
    except (TypeError, ValueError):
        expected = 0
    try:
        filenames = json.loads(request.POST.get('nomes_arquivos') or '[]')
        if not isinstance(filenames, list):
            filenames = []
    except json.JSONDecodeError:
        filenames = []
    default_uf = normalize_uf(request.POST.get('uf')) if source_slug == 'sicar' else ''
    prodes_year = request.POST.get('ano_inicial') if source_slug == 'prodes' else None
    if source_slug == 'prodes' and prodes_year not in (None, ''):
        try:
            if int(prodes_year) > timezone.localdate().year:
                return JsonResponse({'ok': False, 'error': 'O ano inicial do PRODES não pode ser futuro.'}, status=400)
        except (TypeError, ValueError):
            return JsonResponse({'ok': False, 'error': 'O ano inicial do PRODES deve ser um número inteiro.'}, status=400)
    try:
        lote = create_sequential_batch(
            source_slug, request.user, expected,
            default_uf=default_uf,
            prodes_start_year=prodes_year,
            filenames=filenames,
        )
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({
        'ok': True,
        'lote_id': lote.pk,
        'upload_url': reverse('administracao:upload_lote_sequencial', args=[lote.pk]),
        'status_url': reverse('administracao:lote_importacao_status', args=[lote.pk]),
        'finalize_url': reverse('administracao:finalizar_lote_sequencial', args=[lote.pk]),
        'detail_url': reverse('administracao:lote_importacao_detalhe', args=[lote.pk]),
    })


@admin_required
def upload_lote_sequencial(request, pk):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método não permitido.'}, status=405)
    uploaded = request.FILES.get('arquivo')
    if uploaded is None:
        return JsonResponse({'ok': False, 'error': 'Nenhum arquivo foi recebido.'}, status=400)
    try:
        item = append_sequential_upload(pk, uploaded, request.user, index=request.POST.get('indice'))
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({
        'ok': True,
        'item_id': item.pk,
        'status': item.status,
        'status_label': item.get_status_display(),
    })


@admin_required
def finalizar_lote_sequencial(request, pk):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método não permitido.'}, status=405)
    try:
        lote = finalize_sequential_batch(pk, request.user)
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({
        'ok': True,
        'status': lote.status,
        'status_label': lote.get_status_display(),
        'detail_url': reverse('administracao:lote_importacao_detalhe', args=[lote.pk]),
    })

@admin_required
def novo_lote_importacao(request):
    initial = {}
    fonte_slug = request.GET.get('fonte', '').strip().lower()
    if fonte_slug in BATCH_FONTE_SLUGS:
        initial['fonte'] = fonte_slug
    locked_source = fonte_slug if fonte_slug in BATCH_FONTE_SLUGS else None
    locked_uf = normalize_uf(request.GET.get('uf')) if locked_source == 'sicar' else ''
    if locked_uf:
        initial['uf'] = locked_uf
    form = ImportacaoLoteForm(
        request.POST or None, request.FILES or None, initial=initial,
        fonte_locked=locked_source, uf_locked=locked_uf,
    )
    if request.method == 'POST' and form.is_valid():
        # Fallback sem JavaScript: aceita somente um arquivo. O fluxo normal da tela
        # usa os endpoints sequenciais para que arquivos múltiplos não sejam
        # enviados todos ao servidor antes do processamento.
        arquivos = form.cleaned_data.get('arquivos') or []
        if len(arquivos) != 1:
            messages.error(request, 'Para vários arquivos use o envio sequencial da própria tela. Recarregue e tente novamente.')
        else:
            prodes_start_year = form.cleaned_data.get('ano_inicial') if form.cleaned_data['fonte'] == 'prodes' else None
            lote = create_batch_from_uploads(
                arquivos, form.cleaned_data['fonte'], request.user,
                default_uf=form.cleaned_data.get('uf', ''),
                prodes_start_year=prodes_start_year,
            )
            if lote.status == LoteImportacao.Status.FALHOU:
                messages.error(request, 'O lote não pôde ser preparado. Consulte o relatório.')
            else:
                messages.success(request, 'Arquivo recebido e colocado na fila de processamento seguro.')
            return redirect('administracao:lote_importacao_detalhe', pk=lote.pk)
    recentes = LoteImportacao.objects.select_related('administrador').filter(oculto_painel=False)[:30]
    maintenance_sources = [
        {'slug': slug, 'label': fonte.label}
        for slug, fonte in FONTE_SLUGS.items()
        if datasets_for_source(slug)
    ]
    return render(
        request,
        'administracao/importacoes/lote_novo.html',
        {
            'form': form, 'lotes': recentes, 'fonte_locked': locked_source,
            'uf_locked': locked_uf,
            'uf_locked_label': UF_NAMES.get(locked_uf, '') if locked_uf else '',
            'fonte_locked_label': (FONTE_SLUGS[locked_source].label if locked_source else ''),
            'maintenance_sources': maintenance_sources,
            'batch_source_accepts': {
                slug: ','.join(sorted(allowed_input_extensions(slug)))
                for slug in BATCH_FONTE_SLUGS
            },
            'batch_source_formats': {
                slug: ', '.join(sorted(allowed_input_extensions(slug)))
                for slug in BATCH_FONTE_SLUGS
            },
        },
    )


@admin_required
def lote_importacao_detalhe(request, pk):
    lote = get_object_or_404(LoteImportacao.objects.select_related('administrador'), pk=pk)
    itens = list(lote.itens.select_related('importacao').all())
    progresso = calculate_batch_progress(lote, itens)
    source_is_sicar = str(lote.fonte) == 'SICAR'
    fase = str((lote.resultado or {}).get('fase') or '').upper()
    contagens = (lote.resultado or {}).get('contagens') or {}
    return render(
        request,
        'administracao/importacoes/lote_detalhe.html',
        {
            'lote': lote, 'itens': itens, 'progresso': progresso,
            'source_is_sicar': source_is_sicar, 'fase': fase, 'contagens': contagens,
            'uf_choices': list(DefinirUfItemLoteForm().fields['uf'].choices),
        },
    )


@admin_required
def definir_uf_item_lote(request, pk, item_pk):
    lote = get_object_or_404(LoteImportacao, pk=pk)
    item = get_object_or_404(ItemLoteImportacao, pk=item_pk, lote=lote)
    if request.method != 'POST' or str(lote.fonte) != 'SICAR':
        return redirect('administracao:lote_importacao_detalhe', pk=lote.pk)
    form = DefinirUfItemLoteForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Selecione uma UF válida.')
        return redirect('administracao:lote_importacao_detalhe', pk=lote.pk)
    if item.status not in {ItemLoteImportacao.Status.REQUER_REVISAO, ItemLoteImportacao.Status.FALHOU}:
        messages.error(request, 'A UF só pode ser ajustada em itens parados para revisão/falha.')
        return redirect('administracao:lote_importacao_detalhe', pk=lote.pk)
    item.uf = normalize_uf(form.cleaned_data['uf'])
    item.status = ItemLoteImportacao.Status.AGUARDANDO_FILA
    item.motivo = 'UF definida manualmente. O conteúdo será confirmado novamente antes da promoção.'
    item.progresso = 0
    item.etapa = 'Aguardando reprocessamento na fila'
    item.iniciado_em = None
    item.finalizado_em = None
    item.importacao = None
    item.save(update_fields=[
        'uf','status','motivo','progresso','etapa','iniciado_em','finalizado_em','importacao'
    ])
    item.fingerprint_conteudo = ''
    item.save(update_fields=['fingerprint_conteudo'])
    resultado = dict(lote.resultado or {})
    resultado['fase'] = 'ANALISE'
    lote.resultado = resultado
    lote.status = LoteImportacao.Status.ANALISANDO
    lote.data_finalizacao = None
    lote.save(update_fields=['resultado','status','data_finalizacao'])
    update_batch_status(lote.pk)
    messages.success(request, f'UF {item.uf} definida. O item voltou para a fila e será validado novamente.')
    return redirect('administracao:lote_importacao_detalhe', pk=lote.pk)


@admin_required
def confirmar_lote_importacao(request, pk):
    lote = get_object_or_404(LoteImportacao, pk=pk)
    if request.method != 'POST':
        return redirect('administracao:lote_importacao_detalhe', pk=lote.pk)
    try:
        confirm_batch_changes(lote.pk, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Alterações confirmadas. O worker iniciou a fase de importação segura.')
    return redirect('administracao:lote_importacao_detalhe', pk=lote.pk)


@admin_required
def reprocessar_falhas_lote(request, pk):
    lote = get_object_or_404(LoteImportacao, pk=pk)
    if request.method != 'POST':
        return redirect('administracao:lote_importacao_detalhe', pk=lote.pk)
    try:
        retry_failed_batch_items(lote.pk, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Falhas técnicas reenfileiradas. No SICAR, os arquivos voltarão primeiro à pré-análise segura.')
    return redirect('administracao:lote_importacao_detalhe', pk=lote.pk)


@admin_required
def reanalisar_revisoes_lote(request, pk):
    lote = get_object_or_404(LoteImportacao, pk=pk)
    if request.method != 'POST':
        return redirect('administracao:lote_importacao_detalhe', pk=lote.pk)
    try:
        retry_review_batch_items(lote.pk, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Itens em revisão voltaram para a fila com a política atual de classificação.')
    return redirect('administracao:lote_importacao_detalhe', pk=lote.pk)


@admin_required
def lote_importacao_status(request, pk):
    lote = get_object_or_404(LoteImportacao, pk=pk)
    itens = list(lote.itens.select_related('importacao').order_by('id'))
    progresso = calculate_batch_progress(lote, itens)
    return JsonResponse({
        'id': lote.pk,
        'status': lote.status,
        'status_label': lote.get_status_display(),
        'fase': str((lote.resultado or {}).get('fase') or '').upper(),
        'progresso': progresso,
        'modo': (lote.resultado or {}).get('modo', ''),
        'arquivos_esperados': int((lote.resultado or {}).get('arquivos_esperados') or 0),
        'arquivos_recebidos': int((lote.resultado or {}).get('arquivos_recebidos') or 0),
        'sequencial_finalizado': bool((lote.resultado or {}).get('sequencial_finalizado')),
        'itens': [
            {
                'id': item.pk,
                'nome_arquivo': item.nome_arquivo,
                'status': item.status,
                'status_label': item.get_status_display(),
                'progresso': item.progresso,
                'etapa': item.etapa,
                'temporario_liberado': 'temporário liberado' in str(item.etapa or '').lower(),
                'limpeza_temporaria_pendente': 'limpeza temporária pendente' in str(item.etapa or '').lower(),
                'uf': item.uf,
                'dataset_label': item.dataset_label,
                'motivo': item.motivo,
                'relatorio_url': (
                    reverse('administracao:importacao_detalhe', args=[item.importacao_id])
                    if item.importacao_id else ''
                ),
            }
            for item in itens
        ],
    })


@admin_required
def lotes_recentes(request):
    lotes = LoteImportacao.objects.select_related('administrador').filter(oculto_painel=False)
    fonte = str(request.GET.get('fonte') or '').strip().upper()
    status = str(request.GET.get('status') or '').strip().upper()
    termo = str(request.GET.get('q') or '').strip()
    if fonte:
        lotes = lotes.filter(fonte=fonte)
    if status:
        lotes = lotes.filter(status=status)
    if termo:
        lotes = lotes.filter(nome_arquivo_original__icontains=termo)

    fontes = []
    seen = set()
    for enum_value in FONTE_SLUGS.values():
        value = str(getattr(enum_value, 'value', enum_value))
        if value in seen:
            continue
        seen.add(value)
        fontes.append((value, getattr(enum_value, 'label', value)))

    return render(
        request,
        'administracao/importacoes/lotes_recentes.html',
        {
            'lotes': lotes[:300],
            'fontes_filtro': fontes,
            'status_filtro': LoteImportacao.Status.choices,
            'filtro_fonte': fonte,
            'filtro_status': status,
            'filtro_q': termo,
        },
    )


@admin_required
def interromper_lote_importacao(request, pk):
    if request.method != 'POST':
        return redirect('administracao:lotes_recentes')
    try:
        lote = request_batch_interruption(pk, request.user)
    except LoteImportacao.DoesNotExist:
        messages.error(request, 'Lote não encontrado.')
    except Exception as exc:
        messages.error(request, str(exc))
    else:
        if lote.status == LoteImportacao.Status.INTERROMPENDO:
            messages.warning(
                request,
                'Interrupção solicitada. O arquivo em execução será encerrado no próximo ponto seguro; '
                'se a publicação atômica já tiver começado, ela termina antes de o lote parar.',
            )
        else:
            messages.success(request, 'Lote interrompido. Nenhum novo item será processado.')
    destino = str(request.POST.get('retorno') or '').strip()
    if destino == 'dashboard':
        return redirect('administracao:dashboard')
    if destino == 'detalhe':
        return redirect('administracao:lote_importacao_detalhe', pk=pk)
    return redirect('administracao:lotes_recentes')


@superadmin_required
def excluir_lote_importacao(request, pk):
    if request.method != 'POST':
        return redirect('administracao:lotes_recentes')
    confirmacao = str(request.POST.get('confirmacao') or '').strip()
    if confirmacao != 'EXCLUIR':
        messages.error(request, 'Confirmação inválida. Digite EXCLUIR para remover o lote da fila do Manage.')
        return redirect('administracao:lotes_recentes')
    try:
        delete_batch_record(pk, request.user)
    except LoteImportacao.DoesNotExist:
        messages.error(request, 'Lote não encontrado.')
    except Exception as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            'Lote removido da fila do Manage. Nenhum dado publicado no PostgreSQL/PostGIS foi excluído ou alterado.',
        )
    return redirect('administracao:lotes_recentes')


@admin_required
def historico_importacoes(request):
    return render(
        request,
        'administracao/importacoes/historico.html',
        {'importacoes': Importacao.objects.select_related('administrador').all()[:300]},
    )


@admin_required
def importacao_detalhe(request, pk):
    importacao = get_object_or_404(Importacao.objects.select_related('administrador'), pk=pk)
    resultado = importacao.resultado or {}
    identidade = importacao.identidade_relatorio or {}
    contexto = importacao.contexto or {}
    classificacao = contexto.get('batch_classification') or {}
    sicar_metadata = (
        resultado.get('metadados_sicar')
        or identidade.get('metadados_sicar')
        or classificacao.get('metadados_sicar')
        or {}
    )
    return render(
        request,
        'administracao/importacoes/detalhe.html',
        {'importacao': importacao, 'sicar_metadata': sicar_metadata},
    )


# Compatibilidade das telas de sincronização existentes no projeto unificado.
@admin_required
def atualizar_ibama_agora(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método não permitido.'}, status=405)
    job, created = enqueue_ibama(user=request.user, origem=FonteSincronizacao.Origem.MANUAL)
    if created:
        messages.success(request, 'Atualização IBAMA adicionada à fila. A base publicada permanece disponível durante o processamento.')
    else:
        messages.info(request, 'Já existe uma atualização IBAMA em andamento.')
    return redirect('administracao:fonte_datasets', fonte_slug='ibama')


@admin_required
def atualizar_incra_agora(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método não permitido.'}, status=405)
    messages.info(request, 'No catálogo unificado atual, INCRA (assentamentos/quilombolas) e SIGEF são atualizados pelos fluxos de importação do painel.')
    return redirect('administracao:fonte_datasets', fonte_slug='incra')


@admin_required
def status_sincronizacao_fonte(request, pk):
    job = get_object_or_404(FonteSincronizacao, pk=pk)
    end = job.finalizado_em or timezone.now()
    elapsed = int((end - job.iniciado_em).total_seconds()) if job.iniciado_em else 0
    return JsonResponse({
        'ok': True,
        'id': job.pk,
        'active': job.ativo,
        'status': job.status,
        'status_label': job.get_status_display(),
        'progress': job.progresso,
        'stage': job.etapa or job.get_status_display(),
        'elapsed_seconds': max(0, elapsed),
        'bytes_downloaded': job.bytes_baixados,
        'remote_records': job.registros_fonte,
        'new_records': job.novos,
        'changed_records': job.alterados,
        'removed_records': job.removidos,
        'error': job.erro,
        'last_activity_at': job.ultima_atividade.isoformat() if job.ultima_atividade else None,
    })
