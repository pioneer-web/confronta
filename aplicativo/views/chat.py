from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from aplicativo.models import AtendimentoCliente, MensagemAtendimento
from aplicativo.permissions import cliente_required


def _somente_cliente(request):
    return getattr(request.acesso_aplicativo, 'origem', '') == 'CLIENTE'


def _serializar_mensagem(mensagem, usuario):
    return {
        'id': mensagem.pk,
        'texto': mensagem.texto,
        'autor_eu': mensagem.autor_id == usuario.pk,
        'autor_nome': mensagem.autor.get_full_name() or mensagem.autor.email,
        'criado_em': timezone.localtime(mensagem.criado_em).strftime('%d/%m/%Y %H:%M'),
    }


@cliente_required
@require_GET
def chat_estado(request):
    if not _somente_cliente(request):
        return HttpResponseForbidden('Chat disponível para clientes.')

    try:
        atendimento = request.user.atendimento_cliente
    except AtendimentoCliente.DoesNotExist:
        return JsonResponse({'ok': True, 'atendimento': None, 'status': None, 'mensagens': [], 'nao_lidas': 0})

    mensagens = list(
        atendimento.mensagens.select_related('autor').order_by('-criado_em', '-id')[:100]
    )
    mensagens.reverse()
    nao_lidas = atendimento.mensagens.exclude(
        autor_id=request.user.pk
    ).filter(lida_em__isnull=True).count()

    return JsonResponse({
        'ok': True,
        'atendimento': atendimento.pk,
        'status': atendimento.status,
        'status_label': atendimento.get_status_display(),
        'mensagens': [_serializar_mensagem(item, request.user) for item in mensagens],
        'nao_lidas': nao_lidas,
    })


@cliente_required
@require_POST
def chat_enviar(request):
    if not _somente_cliente(request):
        return HttpResponseForbidden('Chat disponível para clientes.')

    texto = ' '.join(str(request.POST.get('texto') or '').split())
    if not texto:
        return JsonResponse({'ok': False, 'erro': 'Digite uma mensagem.'}, status=400)
    if len(texto) > 2000:
        return JsonResponse({'ok': False, 'erro': 'A mensagem pode ter no máximo 2.000 caracteres.'}, status=400)

    atendimento, _ = AtendimentoCliente.objects.get_or_create(
        cliente=request.user,
        defaults={'status': AtendimentoCliente.Status.ABERTO},
    )

    if atendimento.status == AtendimentoCliente.Status.ENCERRADO:
        atendimento.status = AtendimentoCliente.Status.ABERTO
        atendimento.atendente = None

    atendimento.ultima_interacao_em = timezone.now()
    atendimento.save(update_fields=['status', 'atendente', 'ultima_interacao_em', 'atualizado_em'])

    mensagem = MensagemAtendimento.objects.create(
        atendimento=atendimento,
        autor=request.user,
        texto=texto,
    )
    return JsonResponse({
        'ok': True,
        'mensagem': _serializar_mensagem(mensagem, request.user),
        'status': atendimento.status,
    })


@cliente_required
@require_POST
def chat_marcar_lido(request):
    if not _somente_cliente(request):
        return HttpResponseForbidden('Chat disponível para clientes.')

    try:
        atendimento = request.user.atendimento_cliente
    except AtendimentoCliente.DoesNotExist:
        return JsonResponse({'ok': True, 'atualizadas': 0})

    atualizadas = atendimento.mensagens.exclude(
        autor_id=request.user.pk
    ).filter(lida_em__isnull=True).update(lida_em=timezone.now())

    return JsonResponse({'ok': True, 'atualizadas': atualizadas})
