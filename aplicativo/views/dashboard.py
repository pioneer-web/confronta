from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from aplicativo.forms import ConsultaCarForm, ConsultaCoordenadaForm, ConsultaGeometriaForm
from aplicativo.permissions import cliente_required
from aplicativo.services import ConsultaCarErro, ConsultaCarService
from aplicativo.services.consulta_geometria import (
    ConsultaGeometriaErro, geometria_de_geojson_texto, geometria_de_upload,
)
from aplicativo.session_keys import SESSION_CAR_ATUAL, SESSION_CONSULTA_ORIGEM
from aplicativo.security import (
    consumir_limite_consulta_car,
    consumir_limite_selecao_car,
    minutos_para_mensagem,
)


def _contexto_base(request, *, form=None, consulta=None, erro_consulta=None):
    acesso = request.acesso_aplicativo
    return {
        'form': form or ConsultaCarForm(),
        'consulta': consulta,
        'erro_consulta': erro_consulta,
        'acesso_aplicativo': acesso,
        'possui_plano': acesso.possui_plano,
        'pode_desenhar_glebas': acesso.pode_desenhar_glebas,
        'consulta_origem': request.session.get(SESSION_CONSULTA_ORIGEM, 'car'),
    }


@cliente_required
def inicio(request):
    """Tela principal do Módulo 2.

    Segurança/privacidade: a seleção do CAR não é lida da query string.
    O identificador é recuperado somente da sessão autenticada, após ter sido
    validado pelo formulário POST de ``nova_consulta``.
    """
    if request.GET:
        # Remove parâmetros antigos ou manipulados da barra de endereço.
        return redirect('aplicativo:inicio')

    acesso = request.acesso_aplicativo
    if not acesso.pode_consultar:
        return render(
            request,
            'aplicativo/dashboard.html',
            _contexto_base(request),
        )

    car_atual = request.session.get(SESSION_CAR_ATUAL)
    if not car_atual:
        return render(
            request,
            'aplicativo/dashboard.html',
            _contexto_base(request),
        )

    limite = consumir_limite_consulta_car(request)
    if not limite.permitido:
        messages.error(
            request,
            f'Limite de consultas atingido. Aguarde aproximadamente {minutos_para_mensagem(limite.repetir_em_segundos)} minuto(s) antes de atualizar ou consultar novamente.',
        )
        return render(request, 'aplicativo/dashboard.html', _contexto_base(request))

    try:
        consulta = ConsultaCarService().executar(car_atual)
    except ConsultaCarErro as exc:
        # Não mantém na sessão um CAR que não pôde ser aberto.
        request.session.pop(SESSION_CAR_ATUAL, None)
        messages.error(request, str(exc))
        return render(
            request,
            'aplicativo/dashboard.html',
            _contexto_base(request, erro_consulta=str(exc)),
        )

    return render(
        request,
        'aplicativo/dashboard.html',
        _contexto_base(request, consulta=consulta),
    )


@cliente_required
@require_http_methods(['GET', 'POST'])
def nova_consulta(request):
    """Seleciona um CAR pela busca superior, sem expô-lo na URL.

    A rota continua existindo para compatibilidade, mas GET retorna para a
    tela principal. POST valida/normaliza o CAR, confirma que ele pode ser
    consultado, grava o identificador canônico na sessão e redireciona para
    ``/mapa/``, onde a tela operacional já existente é aberta.
    """
    acesso = request.acesso_aplicativo
    if not acesso.pode_consultar:
        return redirect('aplicativo:planos')

    if request.method == 'GET':
        return redirect('aplicativo:inicio')

    form = ConsultaCarForm(request.POST)
    if not form.is_valid():
        erro = next(
            (str(item) for erros in form.errors.values() for item in erros),
            'Informe um número de CAR válido.',
        )
        messages.error(request, erro)
        return redirect('aplicativo:inicio')

    car_normalizado = form.cleaned_data['car']

    limite = consumir_limite_selecao_car(request)
    if not limite.permitido:
        messages.error(
            request,
            f'Limite de consultas atingido. Aguarde aproximadamente {minutos_para_mensagem(limite.repetir_em_segundos)} minuto(s) antes de tentar novamente.',
        )
        return redirect('aplicativo:inicio')

    # Validação leve: confirma o CAR sem executar cruzamentos territoriais duas vezes.
    try:
        ConsultaCarService().validar_existencia(car_normalizado)
    except ConsultaCarErro as exc:
        messages.error(request, str(exc))
        return redirect('aplicativo:inicio')

    request.session[SESSION_CAR_ATUAL] = car_normalizado
    request.session[SESSION_CONSULTA_ORIGEM] = 'car'
    request.session.modified = True
    return redirect('aplicativo:inicio')


def _registrar_car_localizado(request, candidatos, *, origem):
    if not candidatos:
        raise ConsultaCarErro('Nenhum CAR foi localizado para a consulta informada.')
    selecionado = candidatos[0]
    request.session[SESSION_CAR_ATUAL] = selecionado['cod_imovel']
    request.session[SESSION_CONSULTA_ORIGEM] = origem
    request.session.modified = True
    if len(candidatos) > 1:
        messages.info(
            request,
            f'{len(candidatos)} CARs foram encontrados. O imóvel com melhor correspondência espacial foi aberto.',
        )
    return selecionado


def _limite_nova_consulta(request):
    limite = consumir_limite_selecao_car(request)
    if limite.permitido:
        return True
    messages.error(
        request,
        f'Limite de consultas atingido. Aguarde aproximadamente {minutos_para_mensagem(limite.repetir_em_segundos)} minuto(s) antes de tentar novamente.',
    )
    return False


@cliente_required
@require_POST
def nova_consulta_coordenada(request):
    if not request.acesso_aplicativo.pode_consultar:
        return redirect('aplicativo:planos')
    form = ConsultaCoordenadaForm(request.POST)
    if not form.is_valid():
        erro = next((str(item) for erros in form.errors.values() for item in erros), 'Informe latitude e longitude válidas.')
        messages.error(request, erro)
        return redirect('aplicativo:inicio')
    if not _limite_nova_consulta(request):
        return redirect('aplicativo:inicio')
    try:
        candidatos = ConsultaCarService().localizar_por_coordenada(
            form.cleaned_data['latitude'], form.cleaned_data['longitude']
        )
        _registrar_car_localizado(request, candidatos, origem='coordenada')
    except ConsultaCarErro as exc:
        messages.error(request, str(exc))
    return redirect('aplicativo:inicio')


@cliente_required
@require_POST
def nova_consulta_arquivo(request):
    if not request.acesso_aplicativo.pode_consultar:
        return redirect('aplicativo:planos')
    arquivo = request.FILES.get('arquivo')
    if arquivo is None:
        messages.error(request, 'Selecione um arquivo KML ou KMZ.')
        return redirect('aplicativo:inicio')
    if not _limite_nova_consulta(request):
        return redirect('aplicativo:inicio')
    try:
        geometria = geometria_de_upload(arquivo)
        candidatos = ConsultaCarService().localizar_por_geometria(geometria)
        _registrar_car_localizado(request, candidatos, origem='arquivo')
    except (ConsultaCarErro, ConsultaGeometriaErro) as exc:
        messages.error(request, str(exc))
    return redirect('aplicativo:inicio')


@cliente_required
@require_POST
def nova_consulta_geometria(request):
    if not request.acesso_aplicativo.pode_consultar:
        return redirect('aplicativo:planos')
    form = ConsultaGeometriaForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'A geometria desenhada não pôde ser consultada.')
        return redirect('aplicativo:inicio')
    if not _limite_nova_consulta(request):
        return redirect('aplicativo:inicio')
    try:
        geometria = geometria_de_geojson_texto(form.cleaned_data['geojson'])
        candidatos = ConsultaCarService().localizar_por_geometria(geometria)
        _registrar_car_localizado(request, candidatos, origem='desenho')
    except (ConsultaCarErro, ConsultaGeometriaErro) as exc:
        messages.error(request, str(exc))
    return redirect('aplicativo:inicio')

