from django.urls import path
from administracao.views import auth, dashboard, importacoes, bases
from administracao.views import administradores, clientes, planos, alertas, avisos_clientes, financeiro, atendimentos

app_name = 'administracao'

urlpatterns = [
    path('login/', auth.login_view, name='login'),
    path('logout/', auth.logout_view, name='logout'),
    path('', dashboard.dashboard, name='dashboard'),

    # Bases e ingestão do Manage Confronta.
    path('bases/', bases.catalogo_bases, name='catalogo_bases'),
    path('bases/excluir-fonte/', bases.excluir_dados_fonte, name='excluir_dados_fonte'),
    path('bases/<slug:source_slug>/<slug:dataset_slug>/limpar/', bases.limpar_dataset, name='limpar_dataset'),
    path('bases/<slug:source_slug>/', bases.base_detalhe, name='base_detalhe'),
    path('importacoes/lotes/', importacoes.lotes_recentes, name='lotes_recentes'),
    path('importacoes/lote/novo/', importacoes.novo_lote_importacao, name='novo_lote_importacao'),
    path('importacoes/lote/sequencial/iniciar/', importacoes.iniciar_lote_sequencial, name='iniciar_lote_sequencial'),
    path('importacoes/lote/<int:pk>/sequencial/upload/', importacoes.upload_lote_sequencial, name='upload_lote_sequencial'),
    path('importacoes/lote/<int:pk>/sequencial/finalizar/', importacoes.finalizar_lote_sequencial, name='finalizar_lote_sequencial'),
    path('importacoes/lote/<int:pk>/', importacoes.lote_importacao_detalhe, name='lote_importacao_detalhe'),
    path('importacoes/lote/<int:pk>/status/', importacoes.lote_importacao_status, name='lote_importacao_status'),
    path('importacoes/lote/<int:pk>/interromper/', importacoes.interromper_lote_importacao, name='interromper_lote_importacao'),
    path('importacoes/lote/<int:pk>/excluir/', importacoes.excluir_lote_importacao, name='excluir_lote_importacao'),
    path('importacoes/lote/<int:pk>/confirmar/', importacoes.confirmar_lote_importacao, name='confirmar_lote_importacao'),
    path('importacoes/lote/<int:pk>/reprocessar-falhas/', importacoes.reprocessar_falhas_lote, name='reprocessar_falhas_lote'),
    path('importacoes/lote/<int:pk>/reanalisar-revisoes/', importacoes.reanalisar_revisoes_lote, name='reanalisar_revisoes_lote'),
    path('importacoes/lote/<int:pk>/item/<int:item_pk>/uf/', importacoes.definir_uf_item_lote, name='definir_uf_item_lote'),
    path('importar/<slug:fonte_slug>/', importacoes.fonte_datasets, name='fonte_datasets'),
    path('importar/ibama/atualizar-agora/', importacoes.atualizar_ibama_agora, name='atualizar_ibama_agora'),
    path('importar/incra/atualizar-agora/', importacoes.atualizar_incra_agora, name='atualizar_incra_agora'),
    path('sincronizacoes/<int:pk>/status/', importacoes.status_sincronizacao_fonte, name='status_sincronizacao_fonte'),
    path('importar/<slug:fonte_slug>/<slug:dataset_slug>/', importacoes.importar_dataset, name='importar_dataset'),
    path('importacoes/', importacoes.historico_importacoes, name='historico_importacoes'),
    path('importacoes/<int:pk>/', importacoes.importacao_detalhe, name='importacao_detalhe'),

    # Administração comercial e de acessos dentro do mesmo painel.
    path('clientes/', clientes.lista_clientes, name='clientes'),
    path('clientes/novo/', clientes.novo_cliente, name='cliente_novo'),
    path('clientes/<int:pk>/editar/', clientes.editar_cliente, name='cliente_editar'),
    path('clientes/<int:pk>/alternar/', clientes.alternar_cliente, name='cliente_alternar'),
    path('planos/', planos.lista_planos, name='planos'),
    path('financeiro/asaas/', financeiro.financeiro_asaas, name='financeiro_asaas'),
    path('planos/novo/', planos.novo_plano, name='plano_novo'),
    path('planos/<int:pk>/editar/', planos.editar_plano, name='plano_editar'),
    path('planos/<int:pk>/alternar/', planos.alternar_plano, name='plano_alternar'),
    path('administradores/', administradores.lista_administradores, name='administradores'),
    path('administradores/novo/', administradores.criar_administrador, name='administrador_novo'),
    path('administradores/<int:pk>/editar/', administradores.editar_administrador, name='administrador_editar'),
    path('alertas/', alertas.alertas, name='alertas'),
    path('alertas/tabelas/<int:pk>/excluir/', alertas.excluir_tabela, name='excluir_tabela'),
    path('avisos-clientes/', avisos_clientes.lista_avisos_clientes, name='avisos_clientes'),
    path('atendimentos/', atendimentos.lista_atendimentos, name='atendimentos'),
    path('atendimentos/<int:pk>/', atendimentos.atendimento_detalhe, name='atendimento_detalhe'),
    path('atendimentos/<int:pk>/estado/', atendimentos.atendimento_estado, name='atendimento_estado'),
    path('avisos-clientes/<int:pk>/alternar/', avisos_clientes.alternar_aviso_cliente, name='aviso_cliente_alternar'),
]
