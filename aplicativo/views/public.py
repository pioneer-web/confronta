from urllib.parse import urlsplit

from django.conf import settings
from django.shortcuts import render

from aplicativo.models import PlanoComercial


def _url_comercial_segura(valor: str) -> str:
    valor = (valor or '').strip()
    if not valor:
        return ''
    parsed = urlsplit(valor)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return ''
    return valor


def home_publica(request):
    """Home institucional pública e responsiva do CONFRONTA."""
    plano = PlanoComercial.objects.filter(slug='confronta', ativo=True).first()
    return render(request, 'aplicativo/home.html', {
        'plano_confronta': plano,
        'planos_comerciais': [plano] if plano else [],
        'contato_comercial_url': _url_comercial_segura(
            settings.CONFRONTA_COMMERCIAL_CONTACT_URL
        ),
    })
