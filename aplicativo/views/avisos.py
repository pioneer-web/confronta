from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from aplicativo.models import AvisoCliente, LeituraAvisoCliente
from aplicativo.permissions import cliente_required


@cliente_required
@require_POST
def marcar_aviso_lido(request, pk):
    aviso = get_object_or_404(
        AvisoCliente.objects.filter(
            Q(destinatario__isnull=True) | Q(destinatario=request.user)
        ),
        pk=pk,
        ativo=True,
    )
    LeituraAvisoCliente.objects.get_or_create(aviso=aviso, usuario=request.user)

    nao_lidos = (
        AvisoCliente.objects
        .filter(ativo=True)
        .filter(Q(destinatario__isnull=True) | Q(destinatario=request.user))
        .exclude(leituras__usuario=request.user)
        .count()
    )
    return JsonResponse({'ok': True, 'aviso_id': aviso.pk, 'nao_lidos': nao_lidos})
