from django.urls import path

from aplicativo.views import (
    ajuda_view,
    conta_view,
    cadastro_view,
    chat_enviar,
    chat_estado,
    chat_marcar_lido,
    exportar_camada_kml,
    exportar_car_kml,
    inicio,
    login_view,
    marcar_aviso_lido,
    logout_view,
    nova_consulta,
    nova_consulta_arquivo,
    nova_consulta_coordenada,
    nova_consulta_geometria,
    planos_view,
)

app_name = 'aplicativo'

urlpatterns = [
    path('login/', login_view, name='login'),
    path('cadastro/', cadastro_view, name='cadastro'),
    path('cadastro/mensal/', cadastro_view, {'modalidade': 'mensal'}, name='cadastro_mensal'),
    path('cadastro/anual/', cadastro_view, {'modalidade': 'anual'}, name='cadastro_anual'),
    path('logout/', logout_view, name='logout'),
    path('planos/', planos_view, name='planos'),
    path('conta/', conta_view, name='conta'),
    path('ajuda/', ajuda_view, name='ajuda'),
    path('alertas/<int:pk>/lido/', marcar_aviso_lido, name='marcar_aviso_lido'),
    path('suporte/chat/estado/', chat_estado, name='chat_estado'),
    path('suporte/chat/enviar/', chat_enviar, name='chat_enviar'),
    path('suporte/chat/lido/', chat_marcar_lido, name='chat_marcar_lido'),
    path('nova/', nova_consulta, name='nova_consulta'),
    path('nova/coordenada/', nova_consulta_coordenada, name='nova_consulta_coordenada'),
    path('nova/arquivo/', nova_consulta_arquivo, name='nova_consulta_arquivo'),
    path('nova/geometria/', nova_consulta_geometria, name='nova_consulta_geometria'),
    path('exportar/car/', exportar_car_kml, name='exportar_car_kml'),
    path('exportar/camada/<slug:camada>/', exportar_camada_kml, name='exportar_camada_kml'),
    path('', inicio, name='inicio'),
]
