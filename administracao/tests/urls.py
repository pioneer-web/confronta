from django.urls import include, path

urlpatterns = [
    path('painel/', include('administracao.urls')),
    path('mapa/', include('aplicativo.urls')),
    path('pagamentos/', include('billing.urls')),
]
