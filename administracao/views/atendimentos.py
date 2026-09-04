from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from administracao.permissions import admin_required
from aplicativo.models import AtendimentoCliente, MensagemAtendimento


def _nome(user):
    return user.get_full_name() or user.email


@admin_required
def lista_atendimentos(request):
    atendimentos = list(
        AtendimentoCliente.objects
        .select_related('cliente', 'atendente')
        .prefetch_related('mensagens')
        .filter(mensagens__isnull=False)
        .distinct()
        .order_by('-ultima_interacao_em')[:200]
    )

    for atendimento in atendimentos:
        itens = list(atendimento.mensagens.all())
        atendimento.ultima_mensagem_obj = itens[-1] if itens else None
        atendimento.nao_lidas_equipe = sum(
            1 for item in itens
            if item.autor_id == atendimento.cliente_id and item.lida_em is None
        )

    return render(request, 'administracao/atendimentos/lista.html', {'atendimentos': atendimentos})


@admin_required
def atendimento_detalhe(request, pk):
    atendimento = get_object_or_404(
        AtendimentoCliente.objects.select_related('cliente', 'atendente'),
        pk=pk,
    )

    atendimento.mensagens.filter(
        autor_id=atendimento.cliente_id,
        lida_em__isnull=True,
    ).update(lida_em=timezone.now())

    if request.method == 'POST':
        acao = str(request.POST.get('acao') or 'mensagem').strip()

        if acao == 'encerrar':
            atendimento.status = AtendimentoCliente.Status.ENCERRADO
            atendimento.save(update_fields=['status', 'atualizado_em'])
            messages.success(request, 'Atendimento encerrado.')
            return redirect('administracao:atendimento_detalhe', pk=atendimento.pk)

        if acao == 'reabrir':
            atendimento.status = AtendimentoCliente.Status.ABERTO
            atendimento.save(update_fields=['status', 'atualizado_em'])
            messages.success(request, 'Atendimento reaberto.')
            return redirect('administracao:atendimento_detalhe', pk=atendimento.pk)

        texto = ' '.join(str(request.POST.get('texto') or '').split())
        if not texto:
            messages.error(request, 'Digite uma resposta.')
            return redirect('administracao:atendimento_detalhe', pk=atendimento.pk)
        if len(texto) > 4000:
            messages.error(request, 'A resposta pode ter no máximo 4.000 caracteres.')
            return redirect('administracao:atendimento_detalhe', pk=atendimento.pk)

        MensagemAtendimento.objects.create(
            atendimento=atendimento,
            autor=request.user,
            texto=texto,
        )
        atendimento.atendente = request.user
        atendimento.status = AtendimentoCliente.Status.EM_ATENDIMENTO
        atendimento.ultima_interacao_em = timezone.now()
        atendimento.save(update_fields=['atendente', 'status', 'ultima_interacao_em', 'atualizado_em'])
        return redirect('administracao:atendimento_detalhe', pk=atendimento.pk)

    return render(request, 'administracao/atendimentos/detalhe.html', {
        'atendimento': atendimento,
        'mensagens_chat': atendimento.mensagens.select_related('autor').all(),
    })


@admin_required
@require_GET
def atendimento_estado(request, pk):
    atendimento = get_object_or_404(
        AtendimentoCliente.objects.select_related('cliente', 'atendente'),
        pk=pk,
    )
    atendimento.mensagens.filter(
        autor_id=atendimento.cliente_id,
        lida_em__isnull=True,
    ).update(lida_em=timezone.now())

    itens = list(
        atendimento.mensagens.select_related('autor').order_by('-criado_em', '-id')[:150]
    )
    itens.reverse()

    return JsonResponse({
        'ok': True,
        'status': atendimento.status,
        'status_label': atendimento.get_status_display(),
        'mensagens': [{
            'id': item.pk,
            'texto': item.texto,
            'cliente': item.autor_id == atendimento.cliente_id,
            'autor': _nome(item.autor),
            'criado_em': timezone.localtime(item.criado_em).strftime('%d/%m/%Y %H:%M'),
        } for item in itens],
    })
