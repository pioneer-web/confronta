import re
import unicodedata


def norm(value):
    value = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def safe_alias_match(detected, alias):
    """Compara nomes de campos sem tornar a identidade permissiva demais.

    Primeiro exige igualdade normalizada. Como Shapefile/DBF historicamente limita
    nomes a 10 caracteres, também aceita truncamento por prefixo quando o trecho
    comum tem pelo menos 8 caracteres. Isso cobre mudanças como nome_projeto ->
    nome_proje sem confundir aliases curtos/genéricos como id, uf, area ou nome.
    """
    left = norm(detected)
    right = norm(alias)
    if not left or not right:
        return False
    if left == right:
        return True
    common = min(len(left), len(right))
    if common < 8:
        return False
    return left.startswith(right) or right.startswith(left)


def find_matching_field(fields, aliases):
    for alias in aliases:
        for field in fields:
            if safe_alias_match(field, alias):
                return field
    return None


def has_alias(fields, aliases):
    return find_matching_field(fields, aliases) is not None
