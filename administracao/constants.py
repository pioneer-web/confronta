from django.db import models


class FonteDados(models.TextChoices):
    SICAR = 'SICAR', 'SICAR'
    IBAMA = 'IBAMA', 'IBAMA'
    ICMBIO = 'ICMBIO', 'ICMBio'
    CNUC = 'CNUC', 'CNUC'
    PRODES = 'PRODES', 'INPE / PRODES'
    INCRA = 'INCRA', 'INCRA'
    SICOR = 'SICOR', 'SICOR / Crédito Rural'
    SIGEF = 'SIGEF', 'SIGEF / INCRA'
    SNCR = 'SNCR', 'SNCR / INCRA'
    FUNAI = 'FUNAI', 'FUNAI / Terras Indígenas'
    FLORESTAS = 'FLORESTAS_PUBLICAS', 'Florestas Públicas'
    DETER = 'DETER', 'INPE / DETER'
    ANA = 'ANA', 'ANA / Outorgas'
    ANM = 'ANM', 'ANM / Processos Minerários'
    ZARC = 'ZARC', 'ZARC'
    MAPBIOMAS = 'MAPBIOMAS', 'MapBiomas'
    FOCOS_CALOR = 'FOCOS_CALOR', 'INPE / Focos de Calor'


FONTE_SLUGS = {
    'sicar': FonteDados.SICAR,
    'ibama': FonteDados.IBAMA,
    'icmbio': FonteDados.ICMBIO,
    'cnuc': FonteDados.CNUC,
    'prodes': FonteDados.PRODES,
    'incra': FonteDados.INCRA,
    'sicor': FonteDados.SICOR,
    'sigef': FonteDados.SIGEF,
    'sncr': FonteDados.SNCR,
    'funai': FonteDados.FUNAI,
}

FONTE_SCHEMAS = {
    FonteDados.SICAR: 'dados_sicar',
    FonteDados.IBAMA: 'dados_ibama',
    FonteDados.ICMBIO: 'dados_icmbio',
    FonteDados.CNUC: 'dados_cnuc',
    FonteDados.PRODES: 'dados_prodes',
    FonteDados.INCRA: 'dados_incra',
    FonteDados.SICOR: 'dados_sicor',
    FonteDados.SIGEF: 'dados_sigef',
    FonteDados.SNCR: 'dados_sncr',
    FonteDados.FUNAI: 'dados_funai',
    FonteDados.FLORESTAS: 'dados_florestas_publicas',
    FonteDados.DETER: 'dados_deter',
    FonteDados.ANA: 'dados_ana',
    FonteDados.ANM: 'dados_anm',
    FonteDados.ZARC: 'dados_zarc',
    FonteDados.MAPBIOMAS: 'dados_mapbiomas',
    FonteDados.FOCOS_CALOR: 'dados_focos_calor',
}

# Todas as fontes técnicas cadastradas podem usar o lote sequencial manual.
# A classificação continua conservadora: fontes com um único perfil técnico
# usam esse perfil diretamente; fontes com múltiplos perfis exigem evidência
# segura de nome/estrutura antes de qualquer escrita.
BATCH_FONTE_SLUGS = dict(FONTE_SLUGS)
