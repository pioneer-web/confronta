import unicodedata

from django.core.cache import cache
from django.db import DatabaseError, connection, transaction
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from aplicativo.permissions import cliente_required
from aplicativo.repositories.territorial import CamadaIndisponivel, RepositorioTerritorial


@never_cache
@cliente_required
@require_GET
def buscar_municipios(request):
    query = unicodedata.normalize('NFD', request.GET.get('q', '').strip())
    query = ''.join(char for char in query if unicodedata.category(char) != 'Mn')[:80]
    if len(query) < 2:
        return JsonResponse({'resultados': []})
    rate_key = f'municipality-search:5min:{request.user.pk}'
    if cache.add(rate_key, 1, timeout=300):
        requests = 1
    else:
        try:
            requests = cache.incr(rate_key)
        except ValueError:
            cache.add(rate_key, 1, timeout=300)
            requests = 1
    if requests > 120:
        return JsonResponse({'erro': 'Muitas buscas de município. Aguarde alguns minutos.'}, status=429)
    cache_key = f'municipios-sicar:v1:{query.casefold()}'
    data = cache.get(cache_key)
    if data is None:
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('statement_timeout', %s, true)", ['5000'])
                data = RepositorioTerritorial().buscar_municipios_sicar(query, limite=10)
        except (CamadaIndisponivel, DatabaseError):
            return JsonResponse({'erro': 'Sugestões de municípios temporariamente indisponíveis.'}, status=503)
        cache.set(cache_key, data, timeout=300)
    return JsonResponse({'resultados': data})
