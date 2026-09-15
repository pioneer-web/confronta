from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from django.conf import settings
from django.http import HttpResponse
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


def _site_url():
    return (
        getattr(
            settings,
            'SEO_SITE_URL',
            'https://www.confronta.com.br'
        )
        .strip()
        .rstrip('/')
    )


def home_publica(request):
    """Home institucional pública do CONFRONTA."""

    plano = PlanoComercial.objects.filter(
        slug='confronta',
        ativo=True,
    ).first()

    return render(
        request,
        'aplicativo/home.html',
        {
            'plano_confronta': plano,
            'planos_comerciais': [plano] if plano else [],
            'contato_comercial_url': _url_comercial_segura(
                getattr(
                    settings,
                    'CONFRONTA_COMMERCIAL_CONTACT_URL',
                    '',
                )
            ),
            'seo_site_url': _site_url(),
            'google_site_verification': getattr(
                settings,
                'GOOGLE_SITE_VERIFICATION',
                '',
            ),
            'google_analytics_id': getattr(
                settings,
                'GOOGLE_ANALYTICS_ID',
                '',
            ),
        },
    )


def robots_txt(request):
    """Regras públicas de rastreamento."""

    site = _site_url()

    conteudo = f"""User-agent: *
Allow: /
Disallow: /mapa/
Disallow: /pagamentos/
Disallow: /painel/
Disallow: /health/

User-agent: GPTBot
Allow: /
Disallow: /mapa/
Disallow: /pagamentos/
Disallow: /painel/

User-agent: ChatGPT-User
Allow: /
Disallow: /mapa/
Disallow: /pagamentos/
Disallow: /painel/

User-agent: ClaudeBot
Allow: /
Disallow: /mapa/
Disallow: /pagamentos/
Disallow: /painel/

User-agent: PerplexityBot
Allow: /
Disallow: /mapa/
Disallow: /pagamentos/
Disallow: /painel/

Sitemap: {site}/sitemap.xml
"""

    return HttpResponse(
        conteudo,
        content_type='text/plain; charset=utf-8',
    )


def sitemap_xml(request):
    """Sitemap inicial do CONFRONTA."""

    site = escape(_site_url())

    conteudo = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>{site}/</loc>
        <changefreq>weekly</changefreq>
        <priority>1.0</priority>
    </url>
</urlset>
"""

    return HttpResponse(
        conteudo,
        content_type='application/xml; charset=utf-8',
    )


def llms_txt(request):
    """Informações públicas para mecanismos de IA."""

    site = _site_url()

    conteudo = f"""# CONFRONTA

> Plataforma brasileira de inteligência territorial rural.

Site oficial: {site}/

## O que é

O CONFRONTA é uma plataforma para consulta e análise territorial
de imóveis rurais.

## Funcionalidades

- Consulta por CAR
- Camadas SICAR disponíveis
- Sobreposição entre CARs
- Embargos IBAMA
- Embargos ICMBio
- PRODES
- APAs
- Terras Indígenas FUNAI
- Assentamentos INCRA
- Territórios Quilombolas
- Glebas de crédito rural SICOR
- Desenho e medição de glebas
- Exportações geoespaciais

## Observação

Os alertas apresentados pelo CONFRONTA são instrumentos de apoio
à análise e não substituem documentos oficiais ou conclusões jurídicas.

## Contato

WhatsApp: +55 87 93300-5214
E-mail: confrontagis@gmail.com
"""

    return HttpResponse(
        conteudo,
        content_type='text/plain; charset=utf-8',
    )
