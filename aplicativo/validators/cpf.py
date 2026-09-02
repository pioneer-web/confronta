import re


class CpfInvalido(ValueError):
    pass


def normalizar_cpf(valor):
    return re.sub(r'\D', '', valor or '')


def validar_cpf(valor):
    cpf = normalizar_cpf(valor)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        raise CpfInvalido('Informe um CPF válido.')

    numeros = [int(digito) for digito in cpf]
    soma_1 = sum(numeros[i] * (10 - i) for i in range(9))
    digito_1 = (soma_1 * 10) % 11
    if digito_1 == 10:
        digito_1 = 0

    soma_2 = sum(numeros[i] * (11 - i) for i in range(10))
    digito_2 = (soma_2 * 10) % 11
    if digito_2 == 10:
        digito_2 = 0

    if numeros[9] != digito_1 or numeros[10] != digito_2:
        raise CpfInvalido('Informe um CPF válido.')
    return cpf
