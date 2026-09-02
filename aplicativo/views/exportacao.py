import re

from django.http import HttpResponse

from aplicativo.permissions import plano_ativo_required
from aplicativo.services import ExportacaoErro, ExportacaoKmlService
from aplicativo.session_keys import SESSION_CAR_ATUAL
from aplicativo.security import consumir_limite_exportacao, minutos_para_mensagem


def _filename_seguro(valor):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', valor)


def _car_da_sessao(request):
    car = request.session.get(SESSION_CAR_ATUAL)
    return str(car).strip() if car else ''


def _bloqueio_exportacao(request):
    estado = consumir_limite_exportacao(request)
    if estado.permitido:
        return None
    return HttpResponse(
        f'Limite de exportações atingido. Tente novamente em aproximadamente {minutos_para_mensagem(estado.repetir_em_segundos)} minuto(s).',
        status=429,
        content_type='text/plain; charset=utf-8',
    )


@plano_ativo_required
def exportar_car_kml(request):
    bloqueio = _bloqueio_exportacao(request)
    if bloqueio is not None:
        return bloqueio
    car = _car_da_sessao(request)
    if not car:
        return HttpResponse(
            'Nenhum CAR está selecionado nesta sessão.',
            status=400,
            content_type='text/plain; charset=utf-8',
        )

    try:
        conteudo, car_normalizado = ExportacaoKmlService().exportar_imovel(car)
    except ExportacaoErro as exc:
        return HttpResponse(str(exc), status=400, content_type='text/plain; charset=utf-8')

    filename = _filename_seguro(f'CAR_{car_normalizado}.kml')
    response = HttpResponse(conteudo, content_type='application/vnd.google-earth.kml+xml')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@plano_ativo_required
def exportar_camada_kml(request, camada):
    bloqueio = _bloqueio_exportacao(request)
    if bloqueio is not None:
        return bloqueio
    car = _car_da_sessao(request)
    if not car:
        return HttpResponse(
            'Nenhum CAR está selecionado nesta sessão.',
            status=400,
            content_type='text/plain; charset=utf-8',
        )

    try:
        conteudo, label, car_normalizado = ExportacaoKmlService().exportar_camada(car, camada)
    except ExportacaoErro as exc:
        return HttpResponse(str(exc), status=400, content_type='text/plain; charset=utf-8')

    filename = _filename_seguro(f'{label}_{car_normalizado}.kml')
    response = HttpResponse(conteudo, content_type='application/vnd.google-earth.kml+xml')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
