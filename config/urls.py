from django.urls import include, path

from administracao.views.health import health
from aplicativo.views.public import (
    home_publica,
    llms_txt,
    robots_txt,
    sitemap_xml,
)

urlpatterns = [
    path('health/', health, name='health'),

    # SEO / indexação pública
    path('robots.txt', robots_txt, name='robots_txt'),
    path('sitemap.xml', sitemap_xml, name='sitemap_xml'),
    path('llms.txt', llms_txt, name='llms_txt'),

    path('', home_publica, name='public_root'),
    path('mapa/', include('aplicativo.urls')),
    path('pagamentos/', include('billing.urls')),
]
