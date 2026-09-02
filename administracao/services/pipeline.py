import hashlib
import logging
import shutil
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from administracao.datasets import get_dataset
from administracao.models import Alerta, CamadaImportada, Importacao
from .auditoria import registrar_auditoria
from .dataset_identity import validate_dataset_identity
from .dbf_sanitizer import prepare_utf8_shapefile_for_import
from .exceptions import BatchInterruptionRequested, DatasetIdentityError, GISValidationError, ImportacaoError, SecurityValidationError
from .extraction import extract_zip_safely
from .gis_inspector import inspect_all, inspect_dataset
from .postgis import (
    create_staging_schema, drop_schema, import_layer_to_staging,
    inspect_geometry_repairability, inspect_staging_table, promote_dataset,
)
from .schema_drift import compare_schema, snapshot_layer
from .partitioning import UF_CODES, raw_table_for_import, detect_sicar_ufs_in_staging
from .zip_security import run_antivirus, validate_zip, validate_gpkg
from .prodes_filter import DEFAULT_PRODES_START_YEAR, apply_prodes_year_filter, normalize_prodes_start_year
from .content_fingerprint import fingerprint_staging_content

logger = logging.getLogger(__name__)

BATCH_CLASSIFIER_VERSION = 4
DIRECT_VECTOR_SUFFIXES = {'.gpkg', '.geojson', '.json', '.gml', '.kml'}
UPLOAD_SUFFIXES = {'.zip'} | DIRECT_VECTOR_SUFFIXES


def save_uploaded_file(uploaded_file, importacao_id):
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        suffix = '.bin'
    target = Path(settings.QUARANTINE_DIR) / f'{importacao_id}{suffix}'
    sha = hashlib.sha256()
    size = 0
    with target.open('wb') as dst:
        for chunk in uploaded_file.chunks():
            size += len(chunk)
            sha.update(chunk)
            dst.write(chunk)
    return target, sha.hexdigest(), size


def _existing_layer(spec):
    return CamadaImportada.objects.filter(
        fonte=spec.fonte,
        dataset_slug=spec.slug,
        nome_tabela=spec.stable_table,
    ).first()


def _trusted_historical_import(importacao):
    context = importacao.contexto or {}
    if context.get('lote_id') and int(context.get('batch_classifier_version') or 0) < BATCH_CLASSIFIER_VERSION:
        return False
    return True


def _previous_snapshot(spec, current_import_id, context=None):
    previous = None
    for candidate in (
        Importacao.objects.filter(dataset_slug=spec.slug, status=Importacao.Status.CONCLUIDO)
        .exclude(pk=current_import_id)
        .order_by('-data_inicio')[:20]
    ):
        if _trusted_historical_import(candidate):
            previous = candidate
            break
    if previous:
        result = previous.resultado or {}
        snapshot = result.get('schema_snapshot')
        if snapshot:
            return snapshot

    # Compatibilidade com instalações antigas: só usamos CamadaImportada como
    # histórico se sua última promoção não veio de um lote classificado pela
    # política legada potencialmente incorreta.
    existing = _existing_layer(spec)
    if existing and (not existing.ultima_importacao_ref_id or _trusted_historical_import(existing.ultima_importacao_ref)):
        return {
            'layer_name': existing.nome_original,
            'dataset_name': '',
            'fields': [],
            'geometry_type': existing.tipo_geometria,
            'geometry_family': '',
            'crs': '',
            'epsg': existing.srid,
            'signature': existing.assinatura_estrutura,
            'legacy_partial_snapshot': True,
        }
    return None


def _record_failed_drift_alert(spec, drift, importacao, reason):
    if not drift or not drift.get('changed'):
        return
    camada = _existing_layer(spec)
    if not camada:
        return
    message = (
        f'A fonte oficial apresentou alteração estrutural em {spec.label}. '
        f'{drift.get("summary", "")} '
        f'A importação #{importacao.pk} não foi promovida: {reason}. '
        'A base operacional anterior foi mantida.'
    ).strip()
    try:
        Alerta.objects.create(
            tipo=Alerta.Tipo.ALTERACAO_ESTRUTURAL,
            fonte=spec.fonte,
            camada=camada,
            mensagem=message,
        )
    except Exception:
        logger.exception('Não foi possível registrar alerta de alteração estrutural da importação %s', importacao.pk)


def _previous_prodes_content_match(spec, current_import_id, start_year, fingerprint):
    if not fingerprint:
        return None
    # Uma exclusão administrativa preserva o histórico, mas deixa a camada sem uso.
    # Nesse estado, um fingerprint histórico igual não pode impedir a reconstrução
    # da tabela que foi esvaziada explicitamente pelo Superadministrador.
    existing = _existing_layer(spec)
    if not existing or existing.status != CamadaImportada.Status.ATIVA:
        return None
    candidates = (
        Importacao.objects.filter(dataset_slug=spec.slug, status=Importacao.Status.CONCLUIDO)
        .exclude(pk=current_import_id)
        .order_by('-data_inicio')[:20]
    )
    for candidate in candidates:
        result = candidate.resultado or {}
        previous_filter = result.get('filtro_prodes') or {}
        previous_year = previous_filter.get('ano_inicial')
        if previous_year is None:
            previous_year = (candidate.contexto or {}).get('prodes_ano_inicial')
        try:
            previous_year = normalize_prodes_start_year(previous_year)
        except Exception:
            continue
        if previous_year != start_year:
            continue
        previous_fp = result.get('fingerprint_conteudo') or {}
        previous_sha = previous_fp.get('sha256') if isinstance(previous_fp, dict) else str(previous_fp or '')
        if previous_sha and previous_sha == fingerprint.get('sha256'):
            return candidate
    return None


def _previous_raw_content_match(spec, current_import_id, fingerprint):
    expected = str((fingerprint or {}).get('sha256') or '')
    if not expected:
        return None
    existing = _existing_layer(spec)
    if not existing or existing.status != CamadaImportada.Status.ATIVA:
        return None
    candidates = (
        Importacao.objects.filter(dataset_slug=spec.slug, status=Importacao.Status.CONCLUIDO)
        .exclude(pk=current_import_id).order_by('-data_inicio')[:20]
    )
    for candidate in candidates:
        previous = (candidate.resultado or {}).get('fingerprint_conteudo') or {}
        if str(previous.get('sha256') or '') == expected:
            return candidate
    return None


def process_import(uploaded_file, dataset_slug, usuario, context=None, progress_callback=None):
    spec = get_dataset(dataset_slug)
    if not spec:
        raise ValueError('Dataset não cadastrado no CONFRONTA.')

    context = dict(context or {})

    # SICOR é tabular (CSV/GZIP) e possui um fluxo dedicado. Não forçamos
    # estes arquivos pelo pipeline GIS genérico, preservando o mesmo contrato
    # de Importacao, auditoria, RAW e publicação transacional.
    if spec.fonte_slug == 'sicor':
        from .sicor_import import process_sicor_import
        return process_sicor_import(
            uploaded_file, spec, usuario, context=context,
            progress_callback=progress_callback,
        )

    if spec.mode == 'raw_only' and spec.data_kind == 'tabular_flexible':
        from .generic_tabular_import import process_generic_tabular_import
        return process_generic_tabular_import(
            uploaded_file, spec, usuario, context=context,
            progress_callback=progress_callback,
        )

    def progress(percent, stage):
        if progress_callback:
            try:
                progress_callback(percent, stage)
            except BatchInterruptionRequested:
                raise
            except Exception:
                logger.exception('Falha ao registrar progresso da importação %s.', dataset_slug)

    progress(2, 'Registrando importação')

    imp = Importacao.objects.create(
        fonte=spec.fonte,
        dataset_slug=spec.slug,
        dataset_label=spec.label,
        nome_arquivo_original=Path(uploaded_file.name).name,
        hash_sha256='0' * 64,
        tamanho_bytes=0,
        administrador=usuario,
        status=Importacao.Status.RECEBIDO,
        contexto=context,
    )
    staging = None
    extracted = Path(settings.EXTRACTED_DIR) / str(imp.id)
    quarantine = None
    schema_drift = None
    current_snapshot = None
    promoted = False
    text_encoding_repair = None
    prodes_year_filter = {}
    content_fingerprint = {}

    try:
        if settings.MAX_UPLOAD_SIZE_BYTES and uploaded_file.size > settings.MAX_UPLOAD_SIZE_BYTES:
            raise SecurityValidationError('O arquivo excede o limite configurado para upload.')

        progress(8, 'Recebendo arquivo')
        quarantine, digest, size = save_uploaded_file(uploaded_file, imp.id)
        imp.hash_sha256 = digest
        imp.tamanho_bytes = size
        imp.quarantine_path = str(quarantine.relative_to(settings.BASE_DIR))
        imp.status = Importacao.Status.VALIDANDO
        imp.save(update_fields=['hash_sha256', 'tamanho_bytes', 'quarantine_path', 'status'])

        duplicate_qs = Importacao.objects.filter(
            dataset_slug=spec.slug,
            hash_sha256=digest,
            status=Importacao.Status.CONCLUIDO,
        ).exclude(pk=imp.pk).order_by('-data_inicio')
        duplicate = None
        current_batch_version = int(context.get('batch_classifier_version') or 0)
        for candidate in duplicate_qs[:20]:
            # Ao processar um lote com a política nova, não aceitamos como prova
            # de duplicidade uma importação de lote feita pelo classificador v1.
            # Isso permite reparar automaticamente a classificação errada da v0.2.6.
            if current_batch_version >= BATCH_CLASSIFIER_VERSION and not _trusted_historical_import(candidate):
                continue
            if spec.fonte_slug == 'prodes':
                current_year_filter = normalize_prodes_start_year(
                    context.get('prodes_ano_inicial', DEFAULT_PRODES_START_YEAR)
                )
                previous_context = candidate.contexto or {}
                # Importações PRODES antigas, sem o corte temporal registrado, não
                # são prova confiável de duplicidade porque podem conter anos < 2019.
                if 'prodes_ano_inicial' not in previous_context:
                    continue
                if normalize_prodes_start_year(previous_context.get('prodes_ano_inicial')) != current_year_filter:
                    continue
            duplicate = candidate
            break
        # Uma limpeza administrativa mantém o histórico de Importacao, mas
        # invalida a camada ativa. Nesse estado, um SHA antigo não pode impedir
        # a reconstrução do dataset vazio.
        existing_layer = _existing_layer(spec)
        if existing_layer and existing_layer.status != CamadaImportada.Status.ATIVA:
            duplicate = None

        if duplicate and not context.get('force_validate_uf'):
            imp.status = Importacao.Status.IGNORADO_DUPLICADO
            imp.identidade_status = 'DUPLICADO'
            imp.data_finalizacao = timezone.now()
            imp.resultado = {
                'duplicado': True,
                'importacao_anterior_id': duplicate.pk,
                'contexto': context,
                'motivo': 'SHA-256 idêntico a uma importação concluída do mesmo dataset.',
            }
            imp.save(update_fields=['status','identidade_status','data_finalizacao','resultado'])
            registrar_auditoria(
                usuario, 'IMPORTACAO_IGNORADA_DUPLICADA', 'Importacao', imp.pk,
                {'dataset': spec.slug, 'hash_sha256': digest, 'contexto': context, 'anterior': duplicate.pk},
            )
            return imp

        input_suffix = Path(quarantine).suffix.lower()
        raw_spatial = spec.mode == 'raw_only' and spec.data_kind == 'spatial_flexible'
        if input_suffix == '.gpkg' and not (spec.fonte_slug == 'sicar' or raw_spatial):
            raise SecurityValidationError('GeoPackage direto não está habilitado para este perfil.')
        if input_suffix in DIRECT_VECTOR_SUFFIXES - {'.gpkg'} and not raw_spatial:
            raise SecurityValidationError('Formato vetorial direto habilitado apenas para perfis RAW flexíveis.')
        if input_suffix not in UPLOAD_SUFFIXES:
            raise SecurityValidationError('Formato de arquivo não permitido para esta importação.')

        progress(18, 'Validando segurança do arquivo')
        if input_suffix == '.zip':
            security = validate_zip(quarantine)
            antivirus = run_antivirus(quarantine)
            extracted.mkdir(parents=True, exist_ok=True)
            extract_zip_safely(quarantine, extracted)
            layers = inspect_all(extracted)
        elif input_suffix == '.gpkg':
            security = validate_gpkg(quarantine)
            antivirus = run_antivirus(quarantine)
            extracted.mkdir(parents=True, exist_ok=True)
            layers = inspect_dataset(quarantine)
        else:
            # GeoJSON/GML/KML diretos são permitidos somente nos perfis RAW
            # flexíveis. A prova estrutural é a própria abertura pelo GDAL/OGR;
            # não inferimos CRS nem campos caso o arquivo não os declare.
            security = {
                'formato': input_suffix.lstrip('.'),
                'arquivo_bytes': int(size),
                'entrada_direta': True,
            }
            antivirus = run_antivirus(quarantine)
            extracted.mkdir(parents=True, exist_ok=True)
            layers = inspect_dataset(quarantine)

        progress(30, 'Identificando camada e estrutura')

        imp.status = Importacao.Status.VALIDANDO_IDENTIDADE
        imp.save(update_fields=['status'])
        previous_snapshot = _previous_snapshot(spec, imp.pk, context=context)

        identity = validate_dataset_identity(layers, spec, previous_snapshot=previous_snapshot)
        selected_index = int(identity.get('selected_layer_index', 0))
        layer = layers[selected_index]
        current_snapshot = snapshot_layer(layer)
        schema_drift = compare_schema(previous_snapshot, current_snapshot, spec)

        imp.identidade_status = 'CONFIRMADO'
        imp.identidade_relatorio = identity
        imp.save(update_fields=['identidade_status', 'identidade_relatorio'])

        progress(42, 'Validando identidade do dataset')
        imp.status = Importacao.Status.VALIDANDO_GIS
        imp.save(update_fields=['status'])

        # Algumas bases oficiais em Shapefile declaram UTF-8, mas podem conter
        # uma sequência multibyte truncada em um campo DBF. O arquivo oficial
        # extraído nunca é editado: a rotina só atua quando o Shapefile está
        # declarado como UTF-8 E bytes inválidos são comprovadamente detectados.
        # Nesse caso, cria uma cópia operacional temporária, preserva a largura
        # fixa do DBF e não descarta nenhuma feição. A adaptação fica registrada
        # no relatório da importação.
        text_encoding_repair = prepare_utf8_shapefile_for_import(
            layer,
            extracted,
            enabled=True,
        )
        layer['text_encoding_repair'] = text_encoding_repair

        imp.status = Importacao.Status.IMPORTANDO
        imp.save(update_fields=['status'])
        progress(52, 'Carregando staging PostGIS')
        staging = create_staging_schema(imp.id)
        staging_table = raw_table_for_import(spec, context)
        stats = import_layer_to_staging(layer, staging, target_table=staging_table)
        layer['db_stats'] = stats

        if spec.fonte_slug == 'prodes':
            start_year = normalize_prodes_start_year(
                context.get('prodes_ano_inicial', DEFAULT_PRODES_START_YEAR)
            )
            context['prodes_ano_inicial'] = start_year
            imp.contexto = context
            imp.save(update_fields=['contexto'])
            progress(60, f'Filtrando ocorrências PRODES a partir de {start_year}')
            prodes_year_filter = apply_prodes_year_filter(
                staging, staging_table, spec, start_year
            )
            # As validações geométricas e a promoção devem refletir somente os
            # registros que efetivamente poderão chegar à RAW/base operacional.
            stats = inspect_staging_table(staging, staging_table)
            layer['db_stats'] = stats

            progress(63, 'Comparando conteúdo PRODES com a versão atual')
            content_fingerprint = fingerprint_staging_content(
                staging, staging_table, stats.get('geometry_column')
            )
            previous_same_content = _previous_prodes_content_match(
                spec, imp.pk, start_year, content_fingerprint
            )
            if previous_same_content:
                imp.status = Importacao.Status.SEM_ALTERACAO
                imp.data_finalizacao = timezone.now()
                imp.resultado = {
                    'sem_alteracao': True,
                    'motivo': (
                        'O conteúdo PRODES após o filtro temporal é idêntico à última versão confirmada. '
                        'Nenhuma escrita foi realizada na RAW ou na tabela operacional.'
                    ),
                    'importacao_anterior_id': previous_same_content.pk,
                    'seguranca_zip': security,
                    'antimalware': antivirus,
                    'identidade': identity,
                    'contexto': context,
                    'filtro_prodes': prodes_year_filter,
                    'fingerprint_conteudo': content_fingerprint,
                    'schema_snapshot': current_snapshot or snapshot_layer(layer),
                    'alteracoes_estrutura': schema_drift or {},
                }
                imp.save(update_fields=['status', 'data_finalizacao', 'resultado'])
                progress(100, 'Sem alteração — banco preservado')
                registrar_auditoria(
                    usuario, 'PRODES_VERIFICADO_SEM_ALTERACAO', 'Importacao', imp.pk,
                    {
                        'dataset': spec.slug,
                        'ano_inicial': start_year,
                        'fingerprint_conteudo': content_fingerprint,
                        'importacao_anterior_id': previous_same_content.pk,
                    },
                )
                return imp

        if spec.mode == 'raw_only':
            progress(63, 'Comparando conteúdo com a RAW atual')
            content_fingerprint = fingerprint_staging_content(
                staging, staging_table, stats.get('geometry_column')
            )
            previous_same_content = _previous_raw_content_match(spec, imp.pk, content_fingerprint)
            if previous_same_content:
                imp.status = Importacao.Status.SEM_ALTERACAO
                imp.data_finalizacao = timezone.now()
                imp.resultado = {
                    'sem_alteracao': True,
                    'raw_flexivel': True,
                    'operacional_pendente_validacao': True,
                    'motivo': 'O conteúdo recebido é idêntico à última RAW confirmada. Nenhuma escrita foi realizada no banco.',
                    'importacao_anterior_id': previous_same_content.pk,
                    'seguranca_zip': security,
                    'antimalware': antivirus,
                    'identidade': identity,
                    'contexto': context,
                    'fingerprint_conteudo': content_fingerprint,
                    'schema_snapshot': current_snapshot or snapshot_layer(layer),
                    'alteracoes_estrutura': schema_drift or {},
                }
                imp.save(update_fields=['status', 'data_finalizacao', 'resultado'])
                progress(100, 'Sem alteração — RAW preservada')
                registrar_auditoria(
                    usuario, 'RAW_FLEXIVEL_SEM_ALTERACAO', 'Importacao', imp.pk,
                    {'dataset': spec.slug, 'importacao_anterior_id': previous_same_content.pk},
                )
                return imp

        progress(66, 'Validando CRS, geometrias e UF')
        uf_detection = detect_sicar_ufs_in_staging(spec, staging, staging_table)
        layer['uf_detection'] = uf_detection

        expected_uf = str(context.get('uf') or '').strip().upper()
        if spec.fonte_slug == 'sicar' and expected_uf:
            if expected_uf not in UF_CODES:
                raise GISValidationError(
                    f'A UF informada ({expected_uf}) não pertence ao catálogo das 27 UFs brasileiras. '
                    'A atualização estadual foi bloqueada.'
                )
            detected_ufs = set(uf_detection.get('detectadas') or [])
            recognized = int(uf_detection.get('registros_reconheciveis') or 0)
            malformed = int(uf_detection.get('registros_fora_padrao') or 0)
            if not recognized:
                raise GISValidationError(
                    f'Não foi possível confirmar a UF {expected_uf} pelo COD_IMOVEL do arquivo. '
                    'A atualização estadual foi bloqueada e a base anterior foi preservada.'
                )
            if malformed:
                raise GISValidationError(
                    f'Foram encontrados {malformed} registro(s) com COD_IMOVEL fora do padrão esperado. '
                    'A carga estadual foi bloqueada para impedir registros sem partição territorial confiável.'
                )
            # A UF escolhida no painel identifica a partição administrativa que
            # está sendo atualizada, mas arquivos oficiais do SICAR podem conter
            # CARs de UFs vizinhas em regiões de divisa. Isso não invalida a carga.
            # Exigimos apenas que a UF administrativa esteja efetivamente presente
            # no conteúdo; UFs adicionais são aceitas e serão consolidadas por
            # COD_IMOVEL sem apagar a partição completa dos estados vizinhos.
            if expected_uf not in detected_ufs:
                found = ', '.join(sorted(detected_ufs)) or 'nenhuma'
                raise GISValidationError(
                    f'O lote foi associado à UF {expected_uf}, mas essa UF não foi encontrada no conteúdo. '
                    f'UFs confirmadas: {found}. Nada foi promovido porque o arquivo não confirma '
                    'a partição administrativa selecionada.'
                )
            uf_detection['uf_administrativa'] = expected_uf
            uf_detection['ufs_adicionais_aceitas'] = sorted(detected_ufs - {expected_uf})

        geometry_repair = inspect_geometry_repairability(staging, staging_table)
        layer['geometry_repair'] = geometry_repair
        invalid_count = stats['geometrias_invalidas']
        if spec.mode != 'raw_only' and invalid_count and not settings.AUTO_REPAIR_INVALID_GEOMETRIES:
            raise GISValidationError(
                f'Foram encontradas {invalid_count} geometrias inválidas e o reparo automático está desabilitado. '
                'A RAW foi preservada e nenhuma alteração foi feita na base operacional.'
            )
        # Geometrias inválidas reparáveis seguem para a operacional após ST_MakeValid.
        # As realmente irrecuperáveis permanecem integralmente na RAW, ficam fora
        # apenas da tabela operacional e são registradas como pendência no relatório.

        progress(78, 'Publicando RAW flexível' if spec.mode == 'raw_only' else 'Atualizando base operacional')
        comparison = promote_dataset(
            imp,
            layer,
            staging,
            spec,
            schema_drift=schema_drift,
            raw_table_name=staging_table,
            partition_context={'uf': expected_uf} if spec.fonte_slug == 'sicar' and expected_uf else None,
        )
        promoted = True
        staging = None

        if spec.mode != 'raw_only' and geometry_repair.get('reparaveis'):
            camada = _existing_layer(spec)
            if camada:
                Alerta.objects.filter(
                    camada=camada,
                    tipo=Alerta.Tipo.GEOMETRIA_CORRIGIDA,
                    ativo=True,
                ).update(ativo=False, resolvido_em=timezone.now())
                Alerta.objects.create(
                    tipo=Alerta.Tipo.GEOMETRIA_CORRIGIDA,
                    fonte=spec.fonte,
                    camada=camada,
                    mensagem=(
                        f'{geometry_repair["reparaveis"]} geometria(s) inválida(s) da fonte oficial em {spec.label} '
                        'foram preservadas integralmente na RAW e reparadas automaticamente apenas na tabela operacional. '
                        + (
                            f'{geometry_repair.get("nao_reparaveis", 0)} geometria(s) irrecuperável(is) permaneceram na RAW '
                            'e foram excluídas somente da tabela operacional como pendência rastreável. '
                            if geometry_repair.get('nao_reparaveis') else ''
                        )
                        + 'Nenhuma feição foi ignorada com -skipfailures.'
                    ),
                )

        progress(94, 'Validando resultado')
        imp.status = Importacao.Status.CONCLUIDO
        imp.data_finalizacao = timezone.now()
        imp.resultado = {
            'raw_flexivel': spec.mode == 'raw_only',
            'operacional_pendente_validacao': spec.mode == 'raw_only',
            'seguranca_zip': security,
            'antimalware': antivirus,
            'identidade': identity,
            'metadados_sicar': layer.get('sicar_dictionary') or {},
            'reparo_texto_encoding': text_encoding_repair or {},
            'contexto': context,
            'ufs_sicar_detectadas': layer.get('uf_detection', {}),
            'filtro_prodes': prodes_year_filter,
            'fingerprint_conteudo': content_fingerprint,
            'schema_snapshot': current_snapshot or snapshot_layer(layer),
            'alteracoes_estrutura': schema_drift or {},
            'totais': {
                'camadas_encontradas': len(layers),
                'camadas_importadas': 1,
                'camadas_auxiliares_ignoradas': len(identity.get('selecao_camada', {}).get('camadas_auxiliares_ignoradas', [])),
                'registros': stats['registros'],
                'geometrias_invalidas': stats['geometrias_invalidas'],  # compatibilidade com dashboard v0.2.x
                'geometrias_invalidas_detectadas': stats['geometrias_invalidas'],
                'geometrias_corrigidas_operacional': geometry_repair.get('reparaveis', 0),
                'geometrias_nao_reparaveis': geometry_repair.get('nao_reparaveis', 0),
                'registros_inseridos_operacional': (comparison.get('normalizacao') or {}).get('registros_inseridos_operacional', 0),
                'geometrias_vazias': stats['geometrias_vazias'],
                'geometrias_nulas': stats['geometrias_nulas'],
            },
            'reparo_geometrias': geometry_repair,
            'promocao': comparison,
            'camadas': [
                {
                    'camada': layer['layer_name'],
                    'crs': layer['crs'],
                    'epsg_detectado': layer['epsg_detectado'],
                    'tipo_geometria': stats['tipo_geometria'],
                    'srid_postgis': stats['srid'],
                    'registros': stats['registros'],
                    'encoding_origem': layer.get('source_encoding'),
                    'encoding_forcado_ogr': layer.get('encoding_override'),
                    'encoding_sidecar': layer.get('encoding_sidecar'),
                    'assinatura_estrutura': layer['signature'],
                    'campos': layer.get('field_definitions', []),
                    'metadados_sicar': layer.get('sicar_dictionary') or {},
                }
            ],
        }
        imp.save(update_fields=['status', 'data_finalizacao', 'resultado'])
        progress(100, 'Concluído')
        registrar_auditoria(
            usuario,
            'IMPORTACAO_CONCLUIDA',
            'Importacao',
            imp.pk,
            {
                'fonte': spec.fonte,
                'dataset': spec.slug,
                'arquivo': imp.nome_arquivo_original,
                'hash_sha256': digest,
                'estrutura_alterada': bool(schema_drift and schema_drift.get('changed')),
                'filtro_prodes': prodes_year_filter,
                'fingerprint_conteudo': content_fingerprint,
                    'reparo_texto_encoding': {
                    'aplicado': bool((text_encoding_repair or {}).get('aplicado')),
                    'registros_corrigidos': int((text_encoding_repair or {}).get('registros_corrigidos') or 0),
                },
                'contexto': context,
            },
        )
        return imp

    except SecurityValidationError as exc:
        imp.status = Importacao.Status.REJEITADO_SEGURANCA
        imp.data_finalizacao = timezone.now()
        imp.motivo_rejeicao = str(exc)
        imp.save(update_fields=['status', 'data_finalizacao', 'motivo_rejeicao'])
        registrar_auditoria(
            usuario,
            'IMPORTACAO_BLOQUEADA_SEGURANCA',
            'Importacao',
            imp.pk,
            {'dataset': spec.slug, 'motivo': str(exc)},
        )
        return imp

    except DatasetIdentityError as exc:
        imp.status = Importacao.Status.REJEITADO_IDENTIDADE
        imp.identidade_status = exc.report.get('status', 'NAO_CONFIRMADO')
        imp.identidade_relatorio = exc.report
        imp.data_finalizacao = timezone.now()
        imp.motivo_rejeicao = str(exc)
        if current_snapshot:
            imp.resultado = {
                'schema_snapshot_recebido': current_snapshot,
                'alteracoes_estrutura': schema_drift or {},
                'reparo_texto_encoding': text_encoding_repair or {},
                'metadados_sicar': (layer.get('sicar_dictionary') if 'layer' in locals() else {}) or {},
            }
        imp.save(
            update_fields=[
                'status',
                'identidade_status',
                'identidade_relatorio',
                'data_finalizacao',
                'motivo_rejeicao',
                'resultado',
            ]
        )
        _record_failed_drift_alert(spec, schema_drift, imp, 'identidade do dataset não confirmada')
        registrar_auditoria(
            usuario,
            'IMPORTACAO_BLOQUEADA_IDENTIDADE',
            'Importacao',
            imp.pk,
            {'dataset': spec.slug, 'motivo': str(exc), 'relatorio': exc.report},
        )
        return imp

    except BatchInterruptionRequested as exc:
        imp.status = Importacao.Status.INTERROMPIDO
        imp.data_finalizacao = timezone.now()
        imp.motivo_rejeicao = str(exc)
        imp.resultado = {
            'interrompida': True,
            'base_ativa_preservada': not promoted,
            'contexto': context,
        }
        imp.save(update_fields=['status', 'data_finalizacao', 'motivo_rejeicao', 'resultado'])
        registrar_auditoria(
            usuario, 'IMPORTACAO_INTERROMPIDA', 'Importacao', imp.pk,
            {'dataset': spec.slug, 'motivo': str(exc), 'base_ativa_preservada': not promoted},
        )
        return imp

    except Exception as exc:
        logger.exception('Falha na importação %s', imp.pk)
        imp.status = Importacao.Status.FALHOU
        imp.data_finalizacao = timezone.now()
        imp.motivo_rejeicao = str(exc)
        if current_snapshot:
            imp.resultado = {
                'schema_snapshot_recebido': current_snapshot,
                'alteracoes_estrutura': schema_drift or {},
                'reparo_texto_encoding': text_encoding_repair or {},
                'metadados_sicar': (layer.get('sicar_dictionary') if 'layer' in locals() else {}) or {},
            }
        imp.save(update_fields=['status', 'data_finalizacao', 'motivo_rejeicao', 'resultado'])
        if not promoted:
            _record_failed_drift_alert(spec, schema_drift, imp, str(exc)[:500])
        registrar_auditoria(
            usuario,
            'IMPORTACAO_FALHOU',
            'Importacao',
            imp.pk,
            {'dataset': spec.slug, 'motivo': str(exc)},
        )
        return imp

    finally:
        if staging:
            try:
                drop_schema(staging)
            except Exception:
                logger.exception('Não foi possível remover staging %s', staging)
        if extracted.exists():
            shutil.rmtree(extracted, ignore_errors=True)
        if quarantine and Path(quarantine).exists():
            Path(quarantine).unlink(missing_ok=True)
