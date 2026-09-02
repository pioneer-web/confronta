from django.urls import include, path

from administracao.views.health import health
from aplicativo.views import home_publica

urlpatterns = [
    path('health/', health, name='health'),
    path('', home_publica, name='public_root'),
    path('mapa/', include('aplicativo.urls')),
    path('pagamentos/', include('billing.urls')),
    path('painel/', include('administracao.urls')),
]
