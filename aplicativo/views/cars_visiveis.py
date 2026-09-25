"""Leitura limitada de perímetros SICAR para o viewport do mapa Leaflet."""

import math

from django.core.cache import cache
from django.db import DatabaseError
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from aplicativo.permissions import plano_ativo_required
from aplicativo.repositories.territorial import CamadaIndisponivel, RepositorioTerritorial
from aplicativo.session_keys import SESSION_CAR_ATUAL


ZOOM_MINIMO = 12
ZOOM_MAXIMO = 19
LIMITE_REQUISICOES_5MIN = 60


def _viewport_validado(params):
    try:
        zoom = float(params['zoom'])
        west, south, east, north = (float(params[key]) for key in ('west', 'south', 'east', 'north'))
    except (KeyError, ValueError, TypeError):
        return None

    if not all(map(math.isfinite, (zoom, west, south, east, north))):
        return None
    if not ZOOM_MINIMO <= zoom <= ZOOM_MAXIMO:
        return None
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        return None

    # Até 12 tiles por eixo: cobre telas grandes, mas nunca um estado inteiro.
    extensao_maxima = 360 * 12 / (2 ** zoom)
    if east - west > extensao_maxima or north - south > extensao_maxima:
        return None
    return west, south, east, north


@never_cache
@plano_ativo_required
@require_GET
def cars_visiveis(request):
    viewport = _viewport_validado(request.GET)
    if viewport is None:
        return JsonResponse({'erro': 'BBOX ou zoom inválido.'}, status=400)

    # O cache armazena somente contadores efêmeros, nunca dados SICAR.
    chave = f'cars-visiveis:5min:{request.user.pk}'
    if cache.add(chave, 1, timeout=300):
        tentativas = 1
    else:
        try:
            tentativas = cache.incr(chave)
        except ValueError:  # A chave expirou entre add e incr.
            cache.add(chave, 1, timeout=300)
            tentativas = 1
    if tentativas > LIMITE_REQUISICOES_5MIN:
        return JsonResponse({'erro': 'Limite temporário de visualizações atingido.'}, status=429)

    try:
        resultado = RepositorioTerritorial().buscar_cars_no_bbox(
            *viewport, car_excluido=request.session.get(SESSION_CAR_ATUAL) or '',
        )
    except (CamadaIndisponivel, DatabaseError):
        return JsonResponse({'erro': 'Perímetros temporariamente indisponíveis.'}, status=503)
    return JsonResponse(resultado)
