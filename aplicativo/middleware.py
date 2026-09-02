from django.conf import settings
from django.http import HttpResponse


class LimiteCorpoRequisicaoMiddleware:
    """Rejeita corpos anormalmente grandes nas rotas de cliente e logins.

    Uploads GIS administrativos não passam por esta regra para preservar o
    pipeline atual de importação de bases oficiais.
    """

    METODOS_COM_CORPO = {'POST', 'PUT', 'PATCH'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in self.METODOS_COM_CORPO and self._rota_protegida(request.path):
            if request.path == '/mapa/nova/arquivo/':
                limite = getattr(settings, 'QUERY_GEOMETRY_MAX_UPLOAD_BYTES', 5 * 1024 * 1024) + 256 * 1024
            elif request.path == '/mapa/nova/geometria/':
                limite = getattr(settings, 'QUERY_GEOMETRY_MAX_BODY_BYTES', 2 * 1024 * 1024)
            else:
                limite = getattr(settings, 'PUBLIC_MAX_REQUEST_BODY_BYTES', 65536)
            bruto = request.META.get('CONTENT_LENGTH')
            try:
                tamanho = int(bruto) if bruto else 0
            except (TypeError, ValueError):
                tamanho = 0
            if limite > 0 and tamanho > limite:
                return HttpResponse('Requisição excede o limite permitido.', status=413, content_type='text/plain; charset=utf-8')
        return self.get_response(request)

    @staticmethod
    def _rota_protegida(path: str) -> bool:
        return path.startswith('/mapa/') or path == '/painel/login/'
