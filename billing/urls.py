from django.urls import path

from billing import views

app_name = 'billing'

urlpatterns = [
    path('checkout/iniciar/', views.iniciar_checkout, name='iniciar_checkout'),
    path('assinatura/cancelar/', views.cancelar_assinatura, name='cancelar_assinatura'),
    path('checkout/sucesso/', views.checkout_sucesso, name='checkout_sucesso'),
    path('checkout/status/', views.status_assinatura, name='status_assinatura'),
    path('checkout/cancelado/', views.checkout_cancelado, name='checkout_cancelado'),
    path('checkout/expirado/', views.checkout_expirado, name='checkout_expirado'),
    path('asaas/webhook/', views.webhook_asaas, name='webhook_asaas'),
]
