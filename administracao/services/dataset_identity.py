from administracao.datasets import DATASETS, DatasetSpec
from .exceptions import DatasetIdentityError
from .field_matching import norm, has_alias


def geometry_family(value):
    v = norm(value)
    if 'polygon' in v:
        return 'polygon'
    if 'line' in v or 'curve' in v:
        return 'line'
    if 'point' in v:
        return 'point'
    return v


def _has_alias(fields, aliases):
    return has_alias(fields, aliases)


def _contains_normalized_phrase(haystack, token):
    """Compara tokens de nome por limites, não por substring solta.

    Evita que tokens curtos como APP/RL coincidam por acidente dentro de outro
    nome. O formato normalizado usa '_' como separador, então tratamos o token
    como uma sequência completa de segmentos.
    """
    hay = norm(haystack)
    needle = norm(token)
    if not hay or not needle:
        return False
    return f'_{needle}_' in f'_{hay}_'


def _token_match(layer, spec):
    hay = ' '.join([layer.get('dataset_name', ''), layer.get('layer_name', '')])
    return [t for t in spec.name_tokens if _contains_normalized_phrase(hay, t)]


def _mapped_fields(fields, spec):
    mapped = []
    for field in spec.fields:
        if _has_alias(fields, field.aliases):
            mapped.append(field.canonical)
    return mapped


def score_layer(layer, spec: DatasetSpec):
    fields = layer.get('fields', [])
    family = geometry_family(layer.get('geometry_type'))
    allowed = family in spec.geometry_families
    required = []
    required_groups = [field.aliases for field in spec.fields if field.required] or list(spec.identity_required)
    for group in required_groups:
        required.append({'aliases': list(group), 'ok': _has_alias(fields, group)})
    required_ok = all(x['ok'] for x in required)
    tokens = _token_match(layer, spec)
    signals = [s for s in spec.identity_signals if _has_alias(fields, (s,))]
    mapped = _mapped_fields(fields, spec)

    structural_score = (
        (3 if allowed else 0)
        + (3 if required_ok and required else 0)
        + min(4, len(mapped))
        + min(3, len(signals))
    )
    total_score = structural_score + (3 if tokens else 0)
    coverage = (len(mapped) / len(spec.fields)) if spec.fields else 0
    return {
        'score': total_score,
        'structural_score': structural_score,
        'geometry_ok': allowed,
        'geometry_family': family,
        'required': required,
        'required_ok': required_ok,
        'tokens': tokens,
        'signals': signals,
        'mapped_fields': mapped,
        'mapped_count': len(mapped),
        'field_coverage': round(coverage, 4),
    }


def _historical_signature_match(layer, previous_snapshot):
    return bool(
        previous_snapshot
        and previous_snapshot.get('signature')
        and layer.get('signature')
        and previous_snapshot.get('signature') == layer.get('signature')
    )


def _historical_structure_compatible(layer, previous_snapshot):
    if not previous_snapshot or not previous_snapshot.get('fields'):
        return False
    previous_fields = {norm(item.get('name')) for item in previous_snapshot.get('fields', []) if item.get('name')}
    current_fields = {norm(name) for name in layer.get('fields', []) if name}
    if not previous_fields or not current_fields:
        return False
    overlap = len(previous_fields & current_fields) / max(len(previous_fields), len(current_fields))
    same_geometry_family = geometry_family(previous_snapshot.get('geometry_type')) == geometry_family(layer.get('geometry_type'))
    return same_geometry_family and overlap >= 0.80


def _layer_label(layer):
    dataset = str(layer.get('dataset_name') or '')
    name = str(layer.get('layer_name') or '')
    return f'{dataset}/{name}' if dataset and dataset != name else (name or dataset or 'camada sem nome')


def select_dataset_layer(layers, spec: DatasetSpec, previous_snapshot=None):
    """Seleciona com segurança a camada útil dentro de pacotes oficiais multi-layer.

    Fontes oficiais podem publicar, no mesmo ZIP, a camada principal e camadas
    auxiliares (ex.: CNUC com *_pol e *_pt). A seleção nunca é feita apenas pela
    posição no ZIP: geometria, campos, tokens e histórico são avaliados em conjunto.
    Em empate real, a importação é bloqueada em vez de escolher arbitrariamente.
    """
    if not layers:
        report = {
            'status': 'NAO_CONFIRMADO',
            'dataset': spec.slug,
            'motivo': 'Nenhuma camada vetorial foi encontrada para validação.',
            'camadas': [],
        }
        raise DatasetIdentityError(report['motivo'], report)

    scored = []
    for index, layer in enumerate(layers):
        sc = score_layer(layer, spec)
        historical_exact = _historical_signature_match(layer, previous_snapshot)
        historical_compatible = _historical_structure_compatible(layer, previous_snapshot)
        scored.append({
            'index': index,
            'layer': layer,
            'score': sc,
            'historical_exact': historical_exact,
            'historical_compatible': historical_compatible,
        })

    if len(scored) == 1:
        return scored[0]['index'], {
            'criterio': 'CAMADA_UNICA',
            'camadas_encontradas': 1,
            'camadas_auxiliares_ignoradas': [],
        }

    # Primeiro elimina famílias geométricas que o dataset lógico nunca aceita.
    candidates = [item for item in scored if item['score']['geometry_ok']]
    if not candidates:
        report = {
            'status': 'INCOMPATIVEL',
            'dataset': spec.slug,
            'motivo': 'Nenhuma das camadas do pacote possui geometria compatível com o dataset selecionado.',
            'camadas': [
                {
                    'camada': _layer_label(item['layer']),
                    'geometria': item['layer'].get('geometry_type'),
                    'score': item['score']['score'],
                }
                for item in scored
            ],
        }
        raise DatasetIdentityError(report['motivo'], report)

    exact = [item for item in candidates if item['historical_exact']]
    if len(exact) == 1:
        chosen = exact[0]
        criterion = 'ASSINATURA_HISTORICA_DA_CAMADA'
    else:
        compatible = [item for item in candidates if item['historical_compatible']]
        if len(compatible) == 1:
            chosen = compatible[0]
            criterion = 'ESTRUTURA_HISTORICA_DA_CAMADA'
        else:
            # Campos obrigatórios são um filtro, não apenas pontuação.
            required_candidates = [item for item in candidates if item['score']['required_ok']]
            if required_candidates:
                candidates = required_candidates

            def rank(item):
                sc = item['score']
                return (
                    sc['score'],
                    sc['structural_score'],
                    len(sc['tokens']),
                    sc['mapped_count'],
                    sc['field_coverage'],
                )

            candidates = sorted(candidates, key=rank, reverse=True)
            chosen = candidates[0]
            criterion = 'MELHOR_ASSINATURA_ESTRUTURAL'
            if len(candidates) > 1:
                top = chosen['score']
                second = candidates[1]['score']
                decisive = (
                    top['score'] >= second['score'] + 2
                    or (top['tokens'] and not second['tokens'])
                    or top['mapped_count'] >= second['mapped_count'] + 2
                    or top['structural_score'] >= second['structural_score'] + 2
                )
                if not decisive:
                    report = {
                        'status': 'NAO_CONFIRMADO',
                        'dataset': spec.slug,
                        'motivo': (
                            'O pacote contém mais de uma camada compatível e não foi possível escolher a principal '
                            'com segurança. Nenhuma camada foi importada.'
                        ),
                        'camadas': [
                            {
                                'indice': item['index'],
                                'camada': _layer_label(item['layer']),
                                'geometria': item['layer'].get('geometry_type'),
                                'score': item['score']['score'],
                                'structural_score': item['score']['structural_score'],
                                'tokens': item['score']['tokens'],
                                'mapped_count': item['score']['mapped_count'],
                            }
                            for item in candidates[:5]
                        ],
                    }
                    raise DatasetIdentityError(report['motivo'], report)

    ignored = [
        {
            'indice': item['index'],
            'camada': item['layer'].get('layer_name'),
            'arquivo': item['layer'].get('dataset_name'),
            'geometria': item['layer'].get('geometry_type'),
            'motivo': (
                'tabela auxiliar não espacial ignorada'
                if not item['score']['geometry_ok'] and item['layer'].get('auxiliary_table')
                else 'camada auxiliar ou menos compatível com o dataset selecionado'
            ),
        }
        for item in scored if item['index'] != chosen['index']
    ]
    return chosen['index'], {
        'criterio': criterion,
        'camadas_encontradas': len(scored),
        'camadas_auxiliares_ignoradas': ignored,
    }


def validate_dataset_identity(layers, spec: DatasetSpec, previous_snapshot=None):
    selected_index, layer_selection = select_dataset_layer(layers, spec, previous_snapshot=previous_snapshot)
    layer = layers[selected_index]
    target = score_layer(layer, spec)
    # Mantemos uma lista ampla para a DECISÃO de segurança (evita confirmar
    # estruturas SICAR ambíguas após renomeação) e uma lista mais estrita apenas
    # para EXIBIÇÃO no relatório. Assim COD_IMOVEL/NUM_AREA não poluem o dashboard
    # como falsos "datasets alternativos", sem relaxar a validação interna.
    competitors = []
    display_competitors = []
    for other in DATASETS:
        if other.slug == spec.slug:
            continue
        sc = score_layer(layer, other)
        if sc['structural_score'] >= 6 or sc['score'] >= 9:
            entry = {
                'slug': other.slug,
                'label': other.label,
                'fonte': other.fonte,
                'score': sc['score'],
                'structural_score': sc['structural_score'],
                'mapped_count': sc['mapped_count'],
                'tokens': sc['tokens'],
            }
            competitors.append(entry)
            # Para exibição, exigimos sinal realmente discriminante. Dentro da
            # mesma fonte SICAR, nomes/tokens são fundamentais porque várias
            # camadas oficiais compartilham exatamente os mesmos campos básicos.
            if (
                sc['tokens']
                or (
                    other.fonte != spec.fonte
                    and sc['mapped_count'] >= 3
                    and sc['structural_score'] >= max(8, target['structural_score'] - 1)
                )
            ):
                display_competitors.append(entry)
    competitors.sort(key=lambda x: (x['score'], x['structural_score']), reverse=True)
    display_competitors.sort(key=lambda x: (x['score'], x['structural_score']), reverse=True)

    report = {
        'dataset': spec.slug,
        'dataset_label': spec.label,
        'fonte': spec.fonte,
        'camada': layer.get('layer_name'),
        'arquivo_dataset': layer.get('dataset_name'),
        'selected_layer_index': selected_index,
        'selecao_camada': layer_selection,
        'geometria': layer.get('geometry_type'),
        'campos_detectados': layer.get('fields', []),
        'avaliacao': target,
        'possiveis_outros_datasets': display_competitors[:3],
        'metadados_sicar': layer.get('sicar_dictionary') or {},
    }

    if not target['geometry_ok']:
        report['status'] = 'INCOMPATIVEL'
        report['motivo'] = 'Tipo de geometria incompatível com o dataset selecionado.'
        raise DatasetIdentityError(report['motivo'], report)

    if spec.mode == 'raw_only':
        # Perfil manual flexível: o objetivo é preservar a estrutura oficial na RAW,
        # não criar uma tabela operacional por suposição. A seleção da camada já
        # bloqueou ambiguidades reais em pacotes multi-layer; uma camada espacial
        # compatível pode, portanto, ser recebida mesmo que o órgão renomeie campos.
        report['status'] = 'CONFIRMADO'
        report['score'] = target['score']
        report['criterio_confirmacao'] = 'PERFIL_MANUAL_RAW_FLEXIVEL'
        report['raw_only'] = True
        return report

    historical_match = _historical_signature_match(layer, previous_snapshot)
    if historical_match:
        # O nome da camada/arquivo pode mudar na fonte oficial. Uma assinatura de
        # estrutura já confirmada para este mesmo dataset é evidência forte e
        # independente do nome físico publicado pelo órgão.
        report['status'] = 'CONFIRMADO'
        report['score'] = target['score']
        report['criterio_confirmacao'] = 'ASSINATURA_HISTORICA_CONFIRMADA'
        return report

    historical_compatible = _historical_structure_compatible(layer, previous_snapshot)
    if historical_compatible and target['required_ok']:
        # Permite variações seguras que mudam a assinatura (precisão numérica,
        # Polygon/MultiPolygon, metadados) sem depender do nome físico.
        report['status'] = 'CONFIRMADO'
        report['score'] = target['score']
        report['criterio_confirmacao'] = 'ESTRUTURA_HISTORICA_COMPATIVEL'
        return report

    # Se o nome físico contém um token discriminante de OUTRO dataset e o
    # dataset selecionado não possui token correspondente, não confirmamos pela
    # semelhança estrutural genérica. Ex.: RESERVA_LEGAL.zip enviado em
    # 'Remanescente de Vegetação Nativa'. O sistema sugere o item correto em vez
    # de importar silenciosamente no destino errado.
    token_competitors = [c for c in competitors if c['tokens']]
    if not target['tokens'] and token_competitors:
        best_named = token_competitors[0]
        if (
            best_named['structural_score'] >= max(6, target['structural_score'] - 1)
            and best_named['score'] >= target['score'] + 2
        ):
            report['status'] = 'INCOMPATIVEL'
            report['dataset_sugerido'] = {
                'slug': best_named['slug'],
                'fonte': best_named['fonte'],
                'label': best_named['label'],
            }
            report['motivo'] = (
                f'O arquivo parece corresponder a {best_named["fonte"]} — {best_named["label"]}, '
                f'e não ao item selecionado {spec.fonte} — {spec.label}. '
                'Nada foi importado no dataset selecionado.'
            )
            raise DatasetIdentityError(report['motivo'], report)

    if target['required'] and not target['required_ok']:
        best_other = competitors[0] if competitors else None
        clearly_other = bool(
            best_other
            and (
                best_other['score'] >= target['score'] + 2
                or (best_other['tokens'] and best_other['structural_score'] >= target['structural_score'])
            )
        )
        if clearly_other:
            report['status'] = 'INCOMPATIVEL'
            report['dataset_sugerido'] = {
                'slug': best_other['slug'],
                'fonte': best_other['fonte'],
                'label': best_other['label'],
            }
            report['motivo'] = (
                f'O conteúdo não corresponde a {spec.fonte} — {spec.label}. '
                f'A assinatura é mais compatível com {best_other["fonte"]} — {best_other["label"]}. '
                'Nada foi importado no dataset selecionado.'
            )
        else:
            report['status'] = 'NAO_CONFIRMADO'
            report['motivo'] = 'Campos estruturais mínimos do dataset não foram confirmados.'
        raise DatasetIdentityError(report['motivo'], report)

    # Caminho tradicional: nome é um sinal forte, mas nunca é suficiente sozinho.
    if target['tokens'] and target['structural_score'] >= 6:
        if competitors and competitors[0]['score'] >= target['score'] + 3:
            report['status'] = 'INCOMPATIVEL'
            report['motivo'] = f'O conteúdo é mais compatível com outro dataset conhecido: {competitors[0]["fonte"]} — {competitors[0]["label"]}.'
            raise DatasetIdentityError(report['motivo'], report)
        report['status'] = 'CONFIRMADO'
        report['score'] = target['score']
        report['criterio_confirmacao'] = 'NOME_E_ESTRUTURA'
        return report

    # Novo caminho adaptativo: se o órgão renomear a camada, uma estrutura rica e
    # claramente mais compatível com o item selecionado pode confirmar a identidade.
    structural_competitors = [c for c in competitors if c['structural_score'] >= 6]
    best_other = structural_competitors[0] if structural_competitors else None
    margin = target['structural_score'] - (best_other['structural_score'] if best_other else 0)
    mapped_advantage = target['mapped_count'] - (best_other['mapped_count'] if best_other else 0)
    strong_structure = (
        target['required_ok']
        and target['mapped_count'] >= 3
        and target['structural_score'] >= 9
        and (margin >= 2 or mapped_advantage >= 2)
    )
    if strong_structure:
        report['status'] = 'CONFIRMADO'
        report['score'] = target['score']
        report['criterio_confirmacao'] = 'ESTRUTURA_FORTE_APOS_RENOMEACAO'
        report['nome_fisico_nao_reconhecido'] = True
        return report

    report['status'] = 'NAO_CONFIRMADO'
    report['motivo'] = (
        'A estrutura parece parcialmente compatível, mas a identidade do dataset não pôde ser confirmada com segurança. '
        'Se a fonte oficial tiver renomeado campos essenciais de forma não reconhecida, a alteração exige revisão antes da promoção.'
    )
    raise DatasetIdentityError(report['motivo'], report)
