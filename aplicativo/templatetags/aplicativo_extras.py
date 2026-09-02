from django import template
from django.db.models import Exists, OuterRef

from aplicativo.models import AvisoCliente, LeituraAvisoCliente

register = template.Library()

FIELD_LABELS = {
    'cod_imovel': 'CAR',
    'area_total_ha': 'Área cadastrada (ha)',
    'uf': 'UF',
    'state': 'UF / Estado',
    'municipio': 'Município',
    'codigo_municipio': 'Código do município',
    'modulos_fiscais': 'Módulos fiscais',
    'tipo_imovel': 'Tipo de imóvel',
    'situacao_car': 'Situação CAR',
    'condicao': 'Condição',
    'fonte': 'Fonte',
    'fonte_integrada': 'Fonte(s)',
    'dataset_slug': 'Dataset',
    'data_importacao': 'Importado em',
    'tipo': 'Tipo',
    'nome': 'Nome',
    'situacao': 'Situação',
    'fase': 'Fase',
    'area_ha': 'Área informada (ha)',
    'area_calculada_ha': 'Área calculada na origem (ha)',
    'area_geometria_ha': 'Área calculada da geometria (ha)',
    'area_sobreposta_ha': 'Área dentro do CAR (ha)',
    'percentual_car': 'Percentual do CAR (%)',
    'embargo_id': 'ID do registro',
    'seq_tad': 'Sequencial do termo',
    'numero_embargo': 'Termo / número do embargo',
    'serie_embargo': 'Série do termo',
    'auto_infracao': 'Auto de infração',
    'serie_auto': 'Série do auto',
    'processo': 'Processo administrativo',
    'data_embargo': 'Data do registro de embargo',
    'tipo_area': 'Tipo de área',
    'bioma': 'Bioma',
    'nome_imovel': 'Imóvel informado na fonte',
    'unidade_ibama': 'Unidade IBAMA',
    'descricao_infracao': 'Descrição da infração',
    'descricao_termo': 'Descrição do termo',
    'area_desmatada_informada_ha': 'Área desmatada informada (ha)',
    'area_embargo_informada_ha': 'Área informada do registro (ha)',
    'data_ultima_alteracao': 'Última alteração na fonte',
    'data_base': 'Data-base',
    'uuid': 'UUID',
    'fid_origem': 'ID da origem',
    'path_row': 'Órbita/ponto',
    'main_class': 'Classe principal',
    'class_name': 'Classe',
    'tipo_alerta': 'Tipo PRODES',
    'def_cloud': 'Nuvem',
    'julian_day': 'Dia juliano',
    'image_date': 'Data da imagem',
    'year': 'Ano',
    'area_km': 'Área original (km²)',
    'scene_id': 'Cena',
    'source': 'Fonte PRODES',
    'satellite': 'Satélite',
    'sensor': 'Sensor',
    'tipo_prodes': 'Tipo PRODES legado',
    'codigo': 'Código / SIPRA',
    'modalidade': 'Modalidade',
    'capacidade_familias': 'Capacidade de famílias',
    'quantidade_familias': 'Famílias',
    'data_criacao': 'Data de criação',
    'forma_obtencao': 'Forma de obtenção',
    'data_obtencao': 'Data de obtenção',
    'descricao': 'Descrição',
    'identificacao': 'Identificação',
    'codigo_quilombola': 'Código quilombola',
    'codigo_sr': 'Código regional',
    'data_publicacao': 'Data de publicação',
    'data_titulacao': 'Data de titulação',
    'data_decreto': 'Data do decreto',
    'codigo_sipra': 'Código SIPRA',
    'responsavel': 'Responsável na fonte',
    'esfera': 'Esfera',
    'observacao': 'Observação',
    'uc_id': 'ID / código da UC',
    'codigo_cnuc': 'Código CNUC',
    'wdpa_pid': 'WDPA PID',
    'nome_uc': 'Nome da UC',
    'nome_abreviado': 'Nome abreviado',
    'categoria_manejo': 'Categoria de manejo',
    'grupo_manejo': 'Grupo de manejo',
    'orgao_gestor': 'Órgão gestor',
    'ano_criacao': 'Ano de criação',
    'ato_criacao': 'Ato de criação',
    'outro_ato': 'Outro ato',
    'plano_manejo': 'Plano de manejo',
    'conselho_gestor': 'Conselho gestor',
    'categoria_iucn': 'Categoria IUCN',
    'qualidade_poligono': 'Qualidade do polígono',
    'programa_gestao': 'Programa / projeto de gestão',
    'area_ato_ha': 'Área do ato (ha)',
    'biomas': 'Biomas',
    'gerencia_regional': 'Gerência regional',
    'fuso': 'Fuso de abrangência',
    'demarcacao': 'Demarcação',
    'escala': 'Escala',
    'bioma_predominante': 'Bioma predominante',
    'sigla_categoria': 'Sigla da categoria',
    'dominio': 'Domínio',
    'area_sobreposta_ha': 'Área sobreposta (ha)',
    'percentual_car_consultado': 'Percentual do CAR consultado (%)',
    'percentual_outro_car': 'Percentual do outro CAR (%)',
    'ref_bacen': 'Referência BACEN',
    'nu_ordem': 'Ordem da operação',
    'indice_gleba': 'Índice da gleba',
    'ano_sicor': 'Ano da base SICOR',
    'ano_operacao': 'Ano da operação',
    'dt_emissao': 'Data de emissão',
    'dt_vencimento': 'Data de vencimento',
    'cd_estado': 'UF da operação',
    'cd_fonte_recurso': 'Código da fonte de recurso',
    'cd_empreendimento': 'Código do empreendimento',
    'cd_programa': 'Código do programa',
    'vl_parc_credito': 'Valor da parcela de crédito',
    'vl_area_financ': 'Área financiada informada',
    'vl_area_informada': 'Área informada na operação',
    'vl_juros': 'Taxa/valor de juros informado',
    'origem_gleba_sicor': 'Produto espacial SICOR',
}



@register.filter
def field_label(value):
    key = str(value or '')
    return FIELD_LABELS.get(key, key.replace('_', ' ').strip().capitalize())


@register.filter
def display_value(value):
    if value is None or value == '':
        return 'Não informado'
    if isinstance(value, float):
        return f'{value:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return value


@register.simple_tag
def avisos_cliente(user, limite=20):
    """Retorna os avisos ativos e o contador individual de não lidos."""
    if not getattr(user, 'is_authenticated', False):
        return {'itens': [], 'nao_lidos': 0}

    leitura = LeituraAvisoCliente.objects.filter(
        usuario=user,
        aviso_id=OuterRef('pk'),
    )
    base = AvisoCliente.objects.filter(ativo=True).annotate(lido=Exists(leitura))
    nao_lidos = base.filter(lido=False).count()
    itens = list(base.order_by('-criado_em', '-id')[:int(limite or 20)])
    return {'itens': itens, 'nao_lidos': nao_lidos}
