from __future__ import annotations

from dataclasses import dataclass

from administracao.constants import FONTE_SLUGS
from administracao.datasets import datasets_for_source


@dataclass(frozen=True)
class CatalogItem:
    code: str
    label: str
    note: str = ''
    dataset_slug: str = ''


@dataclass(frozen=True)
class SourceProfile:
    slug: str
    label: str
    organization: str
    areas: tuple[str, ...]
    purpose: str
    official_url: str
    items: tuple[CatalogItem, ...]
    rule: str = ''
    priority: int = 2
    implementation: str = 'PLANEJADO'
    import_source_slug: str = ''
    notes: str = ''

    @property
    def is_importable(self) -> bool:
        return bool(self.import_source_slug and self.import_source_slug in FONTE_SLUGS)

    @property
    def dataset_count(self) -> int:
        if not self.is_importable:
            return 0
        return len(datasets_for_source(self.import_source_slug))


AREA_LABELS = {
    'identificacao': 'Identificação',
    'ambiental': 'Ambiental',
    'fundiario': 'Fundiário',
    'financeiro': 'Financeiro',
    'recursos-naturais': 'Recursos naturais',
    'agricola': 'Agrícola',
}


SOURCE_PROFILES = (
    SourceProfile(
        slug='sicar',
        label='CAR / SICAR',
        organization='SICAR',
        areas=('identificacao', 'ambiental'),
        purpose='Cadastro Ambiental Rural e camadas ambientais declaradas do imóvel.',
        official_url='https://consultapublica.car.gov.br/publico/',
        items=(
            CatalogItem('APPS', 'Área de Preservação Permanente — APP'),
            CatalogItem('AREA_CONSOLIDADA', 'Área Consolidada'),
            CatalogItem('AREA_IMOVEL', 'Perímetro do imóvel'),
            CatalogItem('AREA_POUSIO', 'Área de Pousio'),
            CatalogItem('HIDROGRAFIA', 'Hidrografia'),
            CatalogItem('RESERVA_LEGAL', 'Reserva Legal'),
            CatalogItem('SERVIDAO_ADMINISTRATIVA', 'Servidão Administrativa'),
            CatalogItem('USO_RESTRITO', 'Área de Uso Restrito'),
            CatalogItem('VEGETACAO_NATIVA', 'Vegetação Nativa'),
        ),
        rule='Chave conhecida: COD_IMOVEL. Não assumir uma única feição por COD_IMOVEL em todas as camadas.',
        priority=1,
        implementation='OPERACIONAL',
        import_source_slug='sicar',
    ),
    SourceProfile(
        slug='prodes',
        label='PRODES',
        organization='INPE / TerraBrasilis',
        areas=('ambiental',),
        purpose='Ocorrências de desmatamento e supressão de vegetação nativa.',
        official_url='https://terrabrasilis.dpi.inpe.br/downloads/',
        items=(CatalogItem('PRODES', 'Ocorrências PRODES', 'Manter apenas ocorrências a partir de 2019.'),),
        rule='Regra funcional consolidada: utilizar ocorrências a partir de 2019.',
        priority=1,
        implementation='OPERACIONAL',
        import_source_slug='prodes',
    ),
    SourceProfile(
        slug='ibama',
        label='IBAMA',
        organization='IBAMA',
        areas=('ambiental',),
        purpose='Informações ambientais públicas utilizadas nas análises espaciais do CONFRONTA.',
        official_url='https://dadosabertos.ibama.gov.br/',
        items=(CatalogItem('EMBARGOS', 'Termos / áreas de embargo'),),
        priority=2,
        implementation='OPERACIONAL',
        import_source_slug='ibama',
    ),
    SourceProfile(
        slug='cnuc',
        label='CNUC',
        organization='MMA',
        areas=('ambiental',),
        purpose='Unidades de Conservação para análise de interseção territorial.',
        official_url='https://cnuc.mma.gov.br/map',
        items=(CatalogItem('UNIDADES_CONSERVACAO', 'Unidades de Conservação'),),
        priority=2,
        implementation='OPERACIONAL',
        import_source_slug='cnuc',
    ),
    SourceProfile(
        slug='icmbio',
        label='ICMBio',
        organization='ICMBio',
        areas=('ambiental',),
        purpose='Base ambiental complementar já contemplada no pipeline técnico atual.',
        official_url='https://www.gov.br/icmbio/pt-br/dados-icmbio/dados_geoespaciais',
        items=(
            CatalogItem('AREAS_EMBARGADAS', 'Áreas Embargadas'),
            CatalogItem('UCS_FEDERAIS', 'Unidades de Conservação Federais'),
        ),
        priority=2,
        implementation='OPERACIONAL',
        import_source_slug='icmbio',
        notes='Fonte complementar existente no código atual; não altera a estrutura principal do item 9.',
    ),
    SourceProfile(
        slug='incra',
        label='INCRA — assentamentos e quilombolas',
        organization='INCRA',
        areas=('fundiario',),
        purpose='Projetos de assentamento e áreas quilombolas.',
        official_url='https://acervofundiario.incra.gov.br/',
        items=(
            CatalogItem('ASSENTAMENTOS', 'Projetos de assentamento', dataset_slug='incra-assentamentos'),
            CatalogItem('QUILOMBOLAS', 'Áreas quilombolas', dataset_slug='incra-quilombolas'),
        ),
        priority=2,
        implementation='OPERACIONAL',
        import_source_slug='incra',
    ),
    SourceProfile(
        slug='sigef',
        label='SIGEF',
        organization='INCRA',
        areas=('identificacao', 'fundiario'),
        purpose='Parcelas certificadas e informações fundiárias do SIGEF.',
        official_url='https://sigef.incra.gov.br/',
        items=(CatalogItem('SIGEF', 'Acervo / parcelas SIGEF', 'Entrada manual flexível: a estrutura recebida é preservada na RAW.', 'sigef-parcelas'),),
        priority=1,
        implementation='RAW_FLEXIVEL',
        import_source_slug='sigef',
        notes='Upload manual habilitado em RAW flexível. O modelo operacional será definido após validação de amostra oficial; não são inventados campos ou CRS.',
    ),
    SourceProfile(
        slug='sncr',
        label='SNCR',
        organization='INCRA / Receita Federal',
        areas=('identificacao', 'fundiario'),
        purpose='Dados cadastrais do imóvel rural, titular declarado, condição, percentual, área, município e UF.',
        official_url='https://www.gov.br/receitafederal/pt-br/assuntos/orientacao-tributaria/cadastros/portal-cnir/estatisticas-e-dados-abertos/dados-abertos-sncr',
        items=(CatalogItem('SNCR', 'Dados abertos SNCR', 'CSV/GZIP manual; cabeçalho integral preservado na RAW.', 'sncr-dados-abertos'),),
        priority=1,
        implementation='RAW_FLEXIVEL',
        import_source_slug='sncr',
        notes='Upload manual habilitado em RAW flexível. Campos detalhados e regras operacionais continuam dependentes da validação dos arquivos oficiais.',
    ),
    SourceProfile(
        slug='sicor',
        label='SICOR / Crédito Rural',
        organization='Banco Central do Brasil',
        areas=('financeiro',),
        purpose='Operações de crédito rural e informações complementares para a ficha financeira do imóvel.',
        official_url='https://www.bcb.gov.br/estabilidadefinanceira/tabelas-credito-rural-proagro',
        items=(
            CatalogItem('SICOR_OPERACAO_BASICA', 'Operações contratadas', 'Arquivo anual com campo de UF.', 'sicor-operacao-basica'),
            CatalogItem('SICOR_COMPLEMENTO_OPERACAO_BASICA', 'Complemento da operação básica', 'Arquivo complementar nacional.', 'sicor-complemento-operacao-basica'),
            CatalogItem('SICOR_GLEBAS_CONTRAT', 'Glebas contratadas — coordenadas geodésicas', 'Arquivo nacional de pontos dos perímetros; o Manage reconstrói as glebas em SIRGAS 2000.', 'sicor-glebas-contratadas'),
            CatalogItem('SICOR_GLEBAS_WKT', 'Glebas financiadas em WKT', 'Arquivo anual; geometria WKT em SIRGAS2000.', 'sicor-glebas-wkt'),
            CatalogItem('SICOR_PROPRIEDADES', 'Propriedades rurais', 'Relaciona operações a CAR/SNCR/NIRF quando informados.', 'sicor-propriedades'),
            CatalogItem('SICOR_MUTUARIOS', 'Mutuários / beneficiários', 'Arquivo complementar de beneficiários das operações.', 'sicor-mutuarios'),
        ),
        rule='Importação por perfil. Arquivos .gz são descompactados em streaming para CSV; campos extras permanecem na RAW para tolerar evolução do leiaute do Banco Central.',
        priority=1,
        implementation='OPERACIONAL',
        import_source_slug='sicor',
        notes='O Manage valida o cabeçalho real antes de qualquer escrita. Operações e glebas WKT são mantidas por ano; SICOR_GLEBAS_CONTRAT e arquivos complementares são atualizados como snapshot completo.',
    ),
    SourceProfile(
        slug='funai',
        label='Terras Indígenas',
        organization='FUNAI',
        areas=('ambiental',),
        purpose='Terras Indígenas para análise de interseção e contexto territorial.',
        official_url='https://www.gov.br/funai/pt-br/atuacao/terras-indigenas/geoprocessamento-e-mapas',
        items=(CatalogItem('TERRAS_INDIGENAS', 'Terras Indígenas', 'Perfil operacional confirmado com tis_poligonais.zip; RAW integral preservada e geometria publicada para cruzamentos.', 'funai-terras-indigenas'),),
        priority=2,
        implementation='OPERACIONAL',
        import_source_slug='funai',
        notes='Perfil validado com a estrutura real da FUNAI: EPSG:4674, campos terrai_cod/terrai_nom e charset ISO-8859-1 declarado em .cst.',
    ),
    SourceProfile(
        slug='registrais',
        label='Dados registrais',
        organization='Integrações futuras',
        areas=('identificacao',),
        purpose='Dados registrais disponíveis quando houver integração autorizada.',
        official_url='',
        items=(CatalogItem('REGISTRAIS', 'Dados registrais disponíveis'),),
        priority=3,
        implementation='FUTURO',
        notes='Não assumir base nacional aberta de matrículas. O escopo prevê integrações futuras com ONR, SREI, RI Digital e cadastros do CNJ.',
    ),
)

SOURCE_BY_SLUG = {source.slug: source for source in SOURCE_PROFILES}


def get_source_profile(slug: str) -> SourceProfile | None:
    return SOURCE_BY_SLUG.get(slug)


def sources_for_area(area_slug: str) -> list[SourceProfile]:
    return [source for source in SOURCE_PROFILES if area_slug in source.areas]


def catalog_sections() -> list[dict]:
    sections = []
    for area_slug, label in AREA_LABELS.items():
        sources = sources_for_area(area_slug)
        if not sources:
            continue
        sections.append({'slug': area_slug, 'label': label, 'sources': sources})
    return sections


def catalog_summary() -> dict:
    return {
        'total_sources': len(SOURCE_PROFILES),
        'operational_sources': sum(1 for source in SOURCE_PROFILES if source.implementation == 'OPERACIONAL'),
        'validation_sources': sum(1 for source in SOURCE_PROFILES if source.implementation in {'A_VALIDAR', 'RAW_FLEXIVEL'}),
        'raw_flexible_sources': sum(1 for source in SOURCE_PROFILES if source.implementation == 'RAW_FLEXIVEL'),
        'future_sources': sum(1 for source in SOURCE_PROFILES if source.implementation == 'FUTURO'),
        'priority_1': sum(1 for source in SOURCE_PROFILES if source.priority == 1),
    }
