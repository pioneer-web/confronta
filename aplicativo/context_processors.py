from django.conf import settings

UFS_ATENDIDAS = (
    ('AL', 'Alagoas'),
    ('BA', 'Bahia'),
    ('CE', 'Ceará'),
    ('MA', 'Maranhão'),
    ('PB', 'Paraíba'),
    ('PE', 'Pernambuco'),
    ('PI', 'Piauí'),
    ('RN', 'Rio Grande do Norte'),
    ('SE', 'Sergipe'),
)


def cobertura_comercial(request):
    siglas = ', '.join(sigla for sigla, _ in UFS_ATENDIDAS)
    nomes = ', '.join(nome for _, nome in UFS_ATENDIDAS[:-1]) + f' e {UFS_ATENDIDAS[-1][1]}'
    pergunta = 'O CONFRONTA atende todo o Brasil?'
    resposta = (
        f'Ainda não. Neste momento, o CONFRONTA atende imóveis rurais localizados em {siglas}. '
        'A cobertura será ampliada gradualmente.'
    )
    return {
        'cobertura_comercial': {
            'regiao': 'Região Nordeste',
            'ufs': UFS_ATENDIDAS,
            'siglas': siglas,
            'nomes': nomes,
            'pergunta_faq': pergunta,
            'resposta_faq': resposta,
            'descricao_planos': (
                'Neste momento, a plataforma atende imóveis rurais localizados nos estados de '
                f'{nomes}.'
            ),
        }
    }


def google_oauth(request):
    return {'GOOGLE_OAUTH_ENABLED': settings.GOOGLE_OAUTH_ENABLED}
