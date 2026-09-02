import hashlib
import re
import unicodedata


def normalize_identifier(value: str, prefix: str = 'camada') -> str:
    ascii_value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    cleaned = re.sub(r'[^a-zA-Z0-9_]+', '_', ascii_value).strip('_').lower()
    cleaned = re.sub(r'_+', '_', cleaned)
    if not cleaned:
        cleaned = prefix
    if cleaned[0].isdigit():
        cleaned = f'{prefix}_{cleaned}'
    if len(cleaned) > 55:
        digest = hashlib.sha1(value.encode('utf-8')).hexdigest()[:7]
        cleaned = f'{cleaned[:47]}_{digest}'
    return cleaned[:63]
