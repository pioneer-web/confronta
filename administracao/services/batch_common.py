from administracao.constants import FONTE_SLUGS

def _source_slug_from_value(value):
    for slug, enum_value in FONTE_SLUGS.items():
        if str(getattr(enum_value, 'value', enum_value)) == str(value):
            return slug
    return ''
