import csv
import gzip
import io
import fnmatch
from pathlib import Path

import shutil
import tempfile

from django.conf import settings

from administracao.datasets import datasets_for_source

from .batch_upload import _validate_input_extension
from .dataset_identity import score_layer
from .extraction import extract_zip_safely
from .gis_inspector import inspect_all, inspect_dataset
from .sicar_tracking import (
    detect_sicar_uf_from_layer,
    fingerprint_layer_content,
    get_fingerprint,
)
from .zip_security import validate_gpkg, validate_zip


from administracao.models import Importacao

from .field_matching import norm
from .partitioning import UF_CODES


def _detect_uf(relative_path):
    parts = Path(relative_path).parts[:-1]
    candidates = [str(p).strip().upper() for p in parts if str(p).strip().upper() in UF_CODES]
    return candidates[-1] if candidates else ''


def _detect_uf_hint(relative_path):
    """Usa caminho/nome apenas como dica; o conteúdo ainda confirma a UF no pipeline."""
    path = Path(relative_path)
    candidates = []
    for part in path.parts:
        for token in __import__('re').split(r'[^A-Za-z0-9]+', str(part).upper()):
            if token in UF_CODES:
                candidates.append(token)
    return candidates[-1] if candidates else ''


def _filename_token_hits(filename, spec):
    """Tokens fortes encontrados especificamente no nome do ZIP."""
    hay = norm(Path(filename).name)
    padded = f'_{hay}_'
    hits = []
    for token in spec.name_tokens:
        needle = norm(token)
        if needle and f'_{needle}_' in padded:
            hits.append(token)
    return hits


def _filename_pattern_hits(filename, spec):
    """Padrões oficiais/observados que distinguem arquivos estruturalmente parecidos.

    Aceita tanto padrões simples quanto glob (`*.zip`). A versão anterior
    normalizava `*.zip` como texto e podia deixar de reconhecer arquivos oficiais
    já cadastrados nos DatasetSpec.
    """
    path = Path(filename)
    raw_name = path.name.lower()
    raw_stem = path.stem.lower()
    norm_name = norm(path.name)
    norm_stem = norm(path.stem)
    hits = []
    for pattern in getattr(spec, 'filename_patterns', ()) or ():
        raw_pattern = str(pattern or '').strip().lower()
        if not raw_pattern:
            continue
        clean_pattern = raw_pattern.replace('*', '').replace('?', '')
        explicit_suffix = bool(Path(clean_pattern).suffix)
        normalized_pattern = norm(Path(clean_pattern).stem if explicit_suffix else clean_pattern)
        matched = (
            fnmatch.fnmatch(raw_name, raw_pattern)
            or fnmatch.fnmatch(raw_stem, raw_pattern)
            or (
                not explicit_suffix
                and normalized_pattern
                and (normalized_pattern in norm_stem or normalized_pattern in norm_name)
            )
        )
        if matched:
            hits.append(pattern)
    return hits


def _name_rank(candidate):
    patterns = candidate.get('filename_patterns') or []
    tokens = candidate.get('filename_tokens') or []
    pattern_lengths = [len(norm(value)) for value in patterns]
    token_lengths = [len(norm(value)) for value in tokens]
    return (
        1 if patterns else 0,
        max(pattern_lengths, default=0),
        len(patterns),
        max(token_lengths, default=0),
        len(tokens),
        sum(token_lengths),
    )


def _structural_rank(candidate):
    # Uma tabela auxiliar não espacial de um GeoPackage nunca pode vencer uma
    # camada GIS apenas por compartilhar muitos campos. A geometria compatível
    # é requisito de identidade, e vem antes da pontuação estrutural.
    return (
        bool(candidate.get('geometry_ok')),
        bool(candidate.get('historical_signature')),
        candidate.get('structural_score', 0),
        candidate.get('mapped_count', 0),
        candidate.get('base_score', 0),
    )


def _public_candidates(candidates):
    return [
        {
            'slug': c['spec'].slug,
            'label': c['spec'].label,
            'score': c['score'],
            'structural_score': c['structural_score'],
            'mapped_count': c['mapped_count'],
            'tokens': c['tokens'],
            'tokens_nome_arquivo': c.get('filename_tokens', []),
            'padroes_nome_arquivo': c.get('filename_patterns', []),
            'historico': c['historical_signature'],
        }
        for c in candidates
    ]

# Vers?o da pol?tica de classifica??o dos lotes.
BATCH_CLASSIFIER_VERSION = 9


def _trusted_batch_history(imp):
    context = imp.contexto or {}
    if context.get('lote_id') and int(context.get('batch_classifier_version') or 0) < BATCH_CLASSIFIER_VERSION:
        return False
    return True


def _previous_signatures(spec):
    qs = Importacao.objects.filter(dataset_slug=spec.slug, status=Importacao.Status.CONCLUIDO).order_by('-data_inicio')
    signatures = set()
    checked = 0
    for imp in qs[:20]:
        if not _trusted_batch_history(imp):
            continue
        checked += 1
        snap = (imp.resultado or {}).get('schema_snapshot') or {}
        if snap.get('signature'):
            signatures.add(snap['signature'])
        if checked >= 5:
            break
    return signatures


def classify_archive(archive_path, source_slug, relative_path='', archive_sha256=''):
    """Classifica um ZIP sem permitir que estrutura genérica vença nome específico.

    Política v3:
      1. nome oficial/fortemente discriminante do ZIP;
      2. confirmação por campos + geometria;
      3. histórico confiável;
      4. estrutura somente quando houver margem real.

    Se o nome apontar para um dataset mas a estrutura não o confirmar, o item vai
    para revisão. Nunca desviamos silenciosamente para outro dataset parecido.
    """
    archive_path = Path(archive_path)
    suffix = _validate_input_extension(archive_path.name, source_slug)
    temp_dir = None
    if suffix == '.zip':
        validate_zip(archive_path)
        temp_dir = Path(tempfile.mkdtemp(prefix='confronta_classify_', dir=settings.EXTRACTED_DIR))
        extract_zip_safely(archive_path, temp_dir)
        layers = inspect_all(temp_dir)
    else:
        validate_gpkg(archive_path)
        layers = inspect_dataset(archive_path)
    try:

        def classified_payload(top, criterion, eligible_candidates):
            payload = {
                'status': 'CLASSIFICADO',
                'dataset_slug': top['spec'].slug,
                'dataset_label': top['spec'].label,
                'camada': top['layer_name'],
                'criterio': criterion,
                'classificador_versao': BATCH_CLASSIFIER_VERSION,
                'candidatos': _public_candidates(eligible_candidates[:5]),
            }
            if top.get('filename_tokens'):
                payload['tokens_nome_arquivo'] = top['filename_tokens']
            if top.get('filename_patterns'):
                payload['padroes_nome_arquivo'] = top['filename_patterns']
            if top['spec'].fonte_slug == 'sicar':
                selected_layer = layers[top['layer_index']]
                uf_report = detect_sicar_uf_from_layer(selected_layer, top['spec'])
                payload['sicar_uf'] = uf_report
                payload['metadados_sicar'] = selected_layer.get('sicar_dictionary') or {}

                # Atalho mais barato: depois de identificar dataset + UF, um ZIP
                # byte a byte igual ao último confirmado não precisa reler todos
                # os componentes do Shapefile para recalcular o fingerprint.
                previous = None
                if archive_sha256 and uf_report.get('confiavel') and uf_report.get('uf'):
                    previous = get_fingerprint(uf_report['uf'], top['spec'].slug)
                if previous and previous.hash_arquivo and previous.hash_arquivo == archive_sha256:
                    payload['fingerprint_conteudo'] = previous.hash_conteudo
                    payload['arquivo_identico_ultima_versao'] = True
                else:
                    payload['fingerprint_conteudo'] = fingerprint_layer_content(selected_layer)
                    payload['arquivo_identico_ultima_versao'] = False
            return payload

        candidates = []
        for spec in datasets_for_source(source_slug):
            historical = _previous_signatures(spec)
            filename_hits = _filename_token_hits(archive_path.name, spec)
            filename_pattern_hits = _filename_pattern_hits(archive_path.name, spec)
            best = None
            for index, original_layer in enumerate(layers):
                # A identidade da camada continua avaliando nome interno + nome do
                # arquivo, porém a prioridade do ZIP é calculada separadamente.
                layer = dict(original_layer)
                layer['dataset_name'] = f"{original_layer.get('dataset_name','')} {archive_path.name}"
                scored = score_layer(layer, spec)
                history_bonus = 5 if original_layer.get('signature') in historical else 0
                entry = {
                    'spec': spec,
                    'layer_index': index,
                    'layer_name': original_layer.get('layer_name'),
                    'score': scored['score'] + history_bonus,
                    'base_score': scored['score'],
                    'structural_score': scored['structural_score'],
                    'mapped_count': scored['mapped_count'],
                    'required_ok': scored['required_ok'],
                    'geometry_ok': scored['geometry_ok'],
                    'tokens': scored['tokens'],
                    'filename_tokens': filename_hits,
                    'filename_patterns': filename_pattern_hits,
                    'historical_signature': bool(history_bonus),
                }
                if best is None or _structural_rank(entry) > _structural_rank(best):
                    best = entry
            if best:
                candidates.append(best)

        candidates.sort(
            key=lambda c: (_name_rank(c), _structural_rank(c)),
            reverse=True,
        )
        eligible = [c for c in candidates if c['geometry_ok'] and c['required_ok'] and c['structural_score'] >= 6]

        # Se o próprio nome do ZIP traz evidência específica, ele limita o universo
        # de decisão. Isso corrige o caso SICAR em que COD_IMOVEL/NUM_AREA são
        # compartilhados por quase todas as camadas.
        named = [c for c in candidates if c['filename_tokens'] or c.get('filename_patterns')]
        if named:
            best_name_rank = max(_name_rank(c) for c in named)
            name_leaders = [c for c in named if _name_rank(c) == best_name_rank]
            eligible_named = [c for c in name_leaders if c in eligible]

            if not eligible_named:
                return None, {
                    'status': 'NOME_NAO_CONFIRMADO',
                    'motivo': (
                        'O nome do arquivo aponta para um dataset conhecido, mas os campos/geometria '
                        'não confirmaram essa identidade. O item foi enviado para revisão em vez de ser '
                        'desviado automaticamente para outro dataset.'
                    ),
                    'classificador_versao': BATCH_CLASSIFIER_VERSION,
                    'candidatos': _public_candidates(name_leaders[:5]),
                }

            if len(eligible_named) == 1:
                top = eligible_named[0]
            else:
                eligible_named.sort(key=_structural_rank, reverse=True)
                top = eligible_named[0]
                second = eligible_named[1]
                decisive = (
                    top['historical_signature'] and not second['historical_signature']
                    or top['structural_score'] >= second['structural_score'] + 2
                    or top['mapped_count'] >= second['mapped_count'] + 2
                )
                if not decisive:
                    return None, {
                        'status': 'AMBIGUO',
                        'motivo': (
                            'O nome do arquivo é compatível com mais de um dataset e a estrutura não '
                            'permitiu desempate seguro. Nenhum destino foi escolhido automaticamente.'
                        ),
                        'classificador_versao': BATCH_CLASSIFIER_VERSION,
                        'candidatos': _public_candidates(eligible_named[:5]),
                    }

            return top['spec'], classified_payload(top, 'NOME_OFICIAL_E_ESTRUTURA', eligible)

        if not eligible:
            return None, {
                'status': 'NAO_CLASSIFICADO',
                'motivo': 'Nenhum dataset da fonte selecionada apresentou assinatura estrutural mínima compatível.',
                'classificador_versao': BATCH_CLASSIFIER_VERSION,
                'candidatos': _public_candidates(candidates[:5]),
            }

        # Sem nome reconhecível, histórico confiável é a evidência seguinte.
        historical = [c for c in eligible if c['historical_signature']]
        if len(historical) == 1:
            top = historical[0]
            criterion = 'HISTORICO_CONFIAVEL_E_ESTRUTURA'
        elif len(historical) > 1:
            historical.sort(key=_structural_rank, reverse=True)
            top = historical[0]
            second = historical[1]
            if not (
                top['structural_score'] >= second['structural_score'] + 2
                or top['mapped_count'] >= second['mapped_count'] + 2
            ):
                return None, {
                    'status': 'AMBIGUO',
                    'motivo': 'Mais de um histórico confiável apresentou estrutura semelhante; revisão manual necessária.',
                    'classificador_versao': BATCH_CLASSIFIER_VERSION,
                    'candidatos': _public_candidates(historical[:5]),
                }
            criterion = 'HISTORICO_CONFIAVEL_E_ESTRUTURA'
        else:
            eligible.sort(key=_structural_rank, reverse=True)
            top = eligible[0]
            second = eligible[1] if len(eligible) > 1 else None
            clear_margin = (
                second is None
                or top['structural_score'] >= second['structural_score'] + 2
                or top['mapped_count'] >= second['mapped_count'] + 2
            )
            if not clear_margin:
                return None, {
                    'status': 'AMBIGUO',
                    'motivo': (
                        'Mais de um dataset apresentou estrutura semelhante e não houve nome ou histórico '
                        'confiável para escolher com segurança.'
                    ),
                    'classificador_versao': BATCH_CLASSIFIER_VERSION,
                    'candidatos': _public_candidates(eligible[:5]),
                }
            criterion = 'ESTRUTURA_COM_MARGEM_FORTE'

        return top['spec'], classified_payload(top, criterion, eligible)
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _preclassify_input_name(source_slug, input_path):
    """Define apenas um rótulo preliminar quando o nome é inequívoco.

    A promoção continua exigindo a classificação estrutural completa no worker.
    Esse passo evita que arquivos oficiais conhecidos apareçam como “A identificar”
    enquanto aguardam processamento, sem relaxar a segurança do pipeline.
    """
    specs = datasets_for_source(source_slug)
    if not specs:
        return None, {'status': 'NAO_CLASSIFICADO'}
    if len(specs) == 1:
        spec = specs[0]
        return spec, {
            'status': 'PRE_CLASSIFICADO', 'dataset_slug': spec.slug,
            'dataset_label': spec.label, 'criterio': 'PERFIL_UNICO_DA_FONTE',
        }
    if source_slug == 'sicor':
        return _classify_sicor_input(input_path, specs)
    if source_slug == 'sicar':
        # SICAR possui muitas camadas com nomes/estruturas semelhantes; a análise
        # GIS completa continua sendo obrigatória antes de rotular o dataset.
        return None, {'status': 'AGUARDANDO_ANALISE_GIS'}

    candidates = []
    for spec in specs:
        patterns = _filename_pattern_hits(Path(input_path).name, spec)
        tokens = _filename_token_hits(Path(input_path).name, spec)
        if not patterns and not tokens:
            continue
        candidates.append({
            'spec': spec,
            'rank': (
                1 if patterns else 0,
                max((len(norm(value)) for value in patterns), default=0),
                max((len(norm(value)) for value in tokens), default=0),
                len(patterns), len(tokens),
            ),
        })
    if not candidates:
        return None, {'status': 'AGUARDANDO_CLASSIFICACAO_ESTRUTURAL'}
    candidates.sort(key=lambda value: value['rank'], reverse=True)
    top_rank = candidates[0]['rank']
    leaders = [value['spec'] for value in candidates if value['rank'] == top_rank]
    if len(leaders) != 1:
        return None, {'status': 'NOME_AMBIGUO'}
    spec = leaders[0]
    return spec, {
        'status': 'PRE_CLASSIFICADO', 'dataset_slug': spec.slug,
        'dataset_label': spec.label, 'criterio': 'NOME_OFICIAL_UNIVOCO',
    }


def _sicor_filename_matches(input_path, specs):
    matches = []
    for spec in specs:
        if _filename_pattern_hits(Path(input_path).name, spec):
            matches.append(spec)
    return matches


def _sicor_header_tokens(input_path):
    """Lê somente o cabeçalho CSV, inclusive dentro de GZIP, sem extrair o arquivo inteiro."""
    path = Path(input_path)
    if not path.is_file():
        return set()
    try:
        if path.suffix.lower() == '.gz':
            with gzip.open(path, 'rb') as fh:
                sample = fh.read(256 * 1024)
        else:
            with path.open('rb') as fh:
                sample = fh.read(256 * 1024)
    except (OSError, EOFError):
        return set()
    if not sample:
        return set()
    decoded = None
    for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            decoded = sample.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not decoded:
        return set()
    first_line = decoded.splitlines()[0] if decoded.splitlines() else ''
    if not first_line:
        return set()
    delimiter = max((';', ',', '\t', '|'), key=lambda value: first_line.count(value))
    if first_line.count(delimiter) <= 0:
        return set()
    try:
        headers = next(csv.reader(io.StringIO(first_line), delimiter=delimiter))
    except Exception:
        return set()
    return {norm(value) for value in headers if str(value or '').strip()}


def _classify_sicor_input(input_path, specs):
    matches = _sicor_filename_matches(input_path, specs)
    if len(matches) == 1:
        spec = matches[0]
        return spec, {
            'status': 'CLASSIFICADO',
            'dataset_slug': spec.slug,
            'dataset_label': spec.label,
            'criterio': 'NOME_OFICIAL_SICOR',
            'classificador_versao': BATCH_CLASSIFIER_VERSION,
        }

    headers = _sicor_header_tokens(input_path)
    if headers:
        ranked = []
        for spec in specs:
            required = [field for field in spec.fields if field.required]
            required_hits = 0
            required_total = len(required)
            all_hits = 0
            for field in spec.fields:
                aliases = {norm(alias) for alias in field.aliases}
                hit = bool(aliases & headers)
                if hit:
                    all_hits += 1
                if field.required and hit:
                    required_hits += 1
            # Só consideramos perfil estruturalmente válido quando TODOS os
            # campos obrigatórios confirmados estão presentes.
            if required_total and required_hits == required_total:
                ranked.append((required_hits, all_hits, spec))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        if ranked:
            best_rank = ranked[0][:2]
            leaders = [row[2] for row in ranked if row[:2] == best_rank]
            if len(leaders) == 1:
                spec = leaders[0]
                return spec, {
                    'status': 'CLASSIFICADO',
                    'dataset_slug': spec.slug,
                    'dataset_label': spec.label,
                    'criterio': 'CABECALHO_OFICIAL_SICOR',
                    'classificador_versao': BATCH_CLASSIFIER_VERSION,
                }

    if len(matches) > 1:
        return None, {
            'status': 'AMBIGUO',
            'motivo': 'O arquivo SICOR corresponde a mais de um perfil e o cabeçalho não resolveu o empate.',
            'classificador_versao': BATCH_CLASSIFIER_VERSION,
            'candidatos': [{'dataset_slug': spec.slug, 'label': spec.label} for spec in matches],
        }
    return None, {
        'status': 'NAO_CLASSIFICADO',
        'motivo': (
            'O arquivo SICOR não correspondeu com segurança ao nome nem ao cabeçalho de um perfil oficial. '
            'Nenhum destino foi escolhido automaticamente.'
        ),
        'classificador_versao': BATCH_CLASSIFIER_VERSION,
    }


def _classify_batch_input(input_path, source_slug, relative_path='', archive_sha256=''):
    """Escolhe o perfil técnico do item sem assumir estrutura inexistente.

    - uma fonte com um único dataset não precisa de heurística;
    - SICOR usa os padrões oficiais de nome já cadastrados nos perfis;
    - fontes GIS com múltiplos perfis mantêm o classificador estrutural atual.
    """
    input_path = Path(input_path)
    specs = datasets_for_source(source_slug)
    if not specs:
        return None, {
            'status': 'NAO_CLASSIFICADO',
            'motivo': 'A fonte não possui perfil técnico cadastrado para importação.',
            'classificador_versao': BATCH_CLASSIFIER_VERSION,
        }
    if len(specs) == 1:
        spec = specs[0]
        return spec, {
            'status': 'CLASSIFICADO',
            'dataset_slug': spec.slug,
            'dataset_label': spec.label,
            'criterio': 'PERFIL_UNICO_DA_FONTE',
            'classificador_versao': BATCH_CLASSIFIER_VERSION,
        }
    if source_slug == 'sicor':
        return _classify_sicor_input(input_path, specs)
    return classify_archive(
        input_path, source_slug, relative_path=relative_path, archive_sha256=archive_sha256,
    )


def _year_hint_from_name(filename):
    import re
    years = re.findall(r'(?<!\d)(20\d{2})(?!\d)', str(filename or ''))
    return int(years[-1]) if years else None
