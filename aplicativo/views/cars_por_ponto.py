import math

from django.db import DatabaseError, connection, transaction
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from aplicativo.permissions import cliente_required
from aplicativo.repositories.territorial import CamadaIndisponivel, RepositorioTerritorial


@never_cache
@cliente_required
@require_GET
def cars_por_ponto(request):
    try:
        latitude = float(request.GET['latitude'])
        longitude = float(request.GET['longitude'])
    except (KeyError, ValueError, TypeError):
        return JsonResponse({'erro': 'Informe latitude e longitude válidas.'}, status=400)
    if not math.isfinite(latitude) or not math.isfinite(longitude) or not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        return JsonResponse({'erro': 'Latitude deve estar entre -90 e 90 e longitude entre -180 e 180.'}, status=400)
    rate_key = f'cars-point:5min:{request.user.pk}'
    if cache.add(rate_key, 1, timeout=300):
        requests = 1
    else:
        try:
            requests = cache.incr(rate_key)
        except ValueError:
            cache.add(rate_key, 1, timeout=300)
            requests = 1
    if requests > 60:
        return JsonResponse({'erro': 'Limite temporário de consultas por ponto atingido.'}, status=429)
    try:
        # Um resultado extra permite detectar truncamento sem expor ou carregar
        # todos os perímetros que cobrem o ponto.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, true)", ['5000'])
            cars = RepositorioTerritorial().buscar_cars_por_ponto(latitude, longitude, limite=21)
    except (CamadaIndisponivel, DatabaseError):
        return JsonResponse({'erro': 'Perímetros temporariamente indisponíveis.'}, status=503)
    return JsonResponse({'quantidade': min(len(cars), 20), 'truncada': len(cars) > 20, 'resultados': cars[:20]})
