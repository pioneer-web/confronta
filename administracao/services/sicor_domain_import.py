import csv
import hashlib
import io
from pathlib import Path

from django.db import connection, transaction
from django.utils import timezone

from administracao.constants import FONTE_SCHEMAS
from administracao.models import Importacao, SicorInstituicao, SicorPrograma


_LABELS = {'sicor-instituicoes': 'Instituições', 'sicor-programas': 'Programas'}


def normalize_domain_header(value):
    return str(value or '').strip().lstrip('\ufeff').removeprefix('#').strip().upper()


def _read_csv(path_or_file):
    if hasattr(path_or_file, 'read'):
        content = path_or_file.read()
        if isinstance(content, str):
            content = content.encode('utf-8')
    else:
        content = Path(path_or_file).read_bytes()
    decoded = None
    for encoding in ('utf-8-sig', 'utf-8', 'latin-1'):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError('Não foi possível decodificar o CSV.')
    sample = decoded[:8192]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=';,').delimiter
    except csv.Error:
        delimiter = ';' if sample.count(';') > sample.count(',') else ','
    reader = csv.DictReader(io.StringIO(decoded, newline=''), delimiter=delimiter)
    headers = [normalize_domain_header(name) for name in (reader.fieldnames or [])]
    return headers, reader, encoding, delimiter


def identify_domain_header(path_or_file, dataset_slug=None):
    headers, _reader, _encoding, _delimiter = _read_csv(path_or_file)
    available = set(headers)
    valid = {
        'sicor-instituicoes': {'CNPJ_IF', 'NOME_IF'} <= available,
        'sicor-programas': (
            'DESCRICAO' in available and bool({'CODIGO', 'CD_PROGRAMA'} & available)
        ),
    }
    if dataset_slug:
        if not valid.get(dataset_slug, False):
            raise ValueError(f'Este arquivo não corresponde à tabela de {_LABELS[dataset_slug]} SICOR.')
        return dataset_slug, headers
    matches = [slug for slug, is_valid in valid.items() if is_valid]
    if len(matches) != 1:
        raise ValueError('Cabeçalho não identifica exclusivamente Instituições ou Programas SICOR.')
    return matches[0], headers


def _normalize_numeric_code(value, width):
    code = str(value or '').strip()
    return code.zfill(width) if code.isdecimal() else code


def process_sicor_domain_import(uploaded_file, spec, usuario, context=None):
    filename = Path(uploaded_file.name).name
    raw = uploaded_file.read()
    digest = hashlib.sha256(raw).hexdigest()
    imp = Importacao.objects.create(
        fonte=spec.fonte, dataset_slug=spec.slug, dataset_label=spec.label,
        nome_arquivo_original=filename, hash_sha256=digest, tamanho_bytes=len(raw),
        administrador=usuario, status=Importacao.Status.VALIDANDO,
        contexto=context or {},
    )
    report = {'arquivo': filename, 'registros_importados': 0, 'registros_rejeitados': 0}
    try:
        headers, reader, encoding, delimiter = _read_csv(io.BytesIO(raw))
        identify_domain_header(io.BytesIO(raw), spec.slug)
        rows = []
        for row in reader:
            normalized = {normalize_domain_header(key): str(value or '').strip() for key, value in row.items()}
            if spec.slug == 'sicor-instituicoes':
                key, description = normalized.get('CNPJ_IF', ''), normalized.get('NOME_IF', '')
                if not key or not description:
                    report['registros_rejeitados'] += 1
                    continue
                if key.isdecimal():
                    key = key.zfill(8)
                rows.append(SicorInstituicao(cnpj_if=key, nome_if=description,
                                             segmento_if=normalized.get('SEGMENTO_IF', '')))
            else:
                key = normalized.get('CODIGO') or normalized.get('CD_PROGRAMA', '')
                description = normalized.get('DESCRICAO', '')
                if not key or not description:
                    report['registros_rejeitados'] += 1
                    continue
                rows.append(SicorPrograma(cd_programa=_normalize_numeric_code(key, 4), descricao=description))
        if not rows:
            raise ValueError('Nenhum registro válido foi encontrado; os dados anteriores foram preservados.')
        model = SicorInstituicao if spec.slug == 'sicor-instituicoes' else SicorPrograma
        table = spec.stable_table
        schema = FONTE_SCHEMAS[spec.fonte]
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', [f'{schema}.{table}'])
            model.objects.all().delete()
            model.objects.bulk_create(rows, batch_size=1000)
        report.update(registros_importados=len(rows), encoding=encoding, delimitador=delimiter,
                      destino=f'{schema}.{table}', substituicao='SNAPSHOT_ATOMICO')
        imp.status = Importacao.Status.CONCLUIDO
        imp.motivo_rejeicao = ''
    except Exception as exc:
        imp.status = Importacao.Status.REJEITADO_IDENTIDADE if 'não corresponde' in str(exc) else Importacao.Status.FALHOU
        imp.motivo_rejeicao = str(exc)
    imp.resultado = {'sicor_dominios': report}
    imp.data_finalizacao = timezone.now()
    imp.save(update_fields=['status', 'motivo_rejeicao', 'resultado', 'data_finalizacao'])
    return imp
