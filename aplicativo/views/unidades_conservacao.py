from django.db import DatabaseError
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from aplicativo.permissions import cliente_required
from aplicativo.repositories.territorial import RepositorioTerritorial


@never_cache
@cliente_required
@require_GET
def geometria_completa_uc(request):
    if not request.acesso_aplicativo.pode_consultar:
        return JsonResponse({'erro': 'A consulta de Unidades de Conservação não está disponível.'}, status=403)

    fonte = request.GET.get('fonte', '')
    campo = request.GET.get('campo', '')
    identificador = request.GET.get('identificador', '')
    try:
        feature = RepositorioTerritorial().buscar_geometria_completa_uc(
            fonte, campo, identificador
        )
    except ValueError:
        return JsonResponse({'erro': 'Identificador de Unidade de Conservação inválido.'}, status=400)
    except DatabaseError:
        return JsonResponse({'erro': 'Não foi possível carregar a geometria completa da Unidade de Conservação.'}, status=503)

    if feature is None:
        return JsonResponse({'erro': 'Unidade de Conservação não encontrada.'}, status=404)
    return JsonResponse({'feature': feature})
