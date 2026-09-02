import re


class CarInvalido(ValueError):
    pass


def normalizar_car(valor):
    """Normaliza um CAR para o padrão UF-0000000-IDENTIFICADOR.

    Aceita o código com ou sem hífens, pontos, espaços e outros separadores
    visuais. A rotina não tenta adivinhar partes ausentes: depois de remover
    separadores, o conteúdo precisa possuir 2 letras de UF, 7 dígitos do
    município e 32 caracteres alfanuméricos do identificador.
    """

    bruto = re.sub(r'[^A-Za-z0-9]', '', (valor or '')).upper()
    match = re.fullmatch(r'([A-Z]{2})(\d{7})([A-Z0-9]{32})', bruto)
    if not match:
        raise CarInvalido(
            'Informe um CAR válido. O sistema aceita o código com ou sem pontos e hífens.'
        )
    uf, municipio, identificador = match.groups()
    return f'{uf}-{municipio}-{identificador}'
