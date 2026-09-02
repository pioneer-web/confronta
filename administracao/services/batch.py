import hashlib
import csv
import fnmatch
import gzip
import io
from datetime import timedelta
import logging
import os
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from administracao.constants import FONTE_SLUGS, BATCH_FONTE_SLUGS
from administracao.datasets import datasets_for_source
from administracao.models import (
    Importacao, ItemLoteImportacao, LoteImportacao, SicarEstado, SicarFingerprintCamada,
)
from .auditoria import registrar_auditoria
from .dataset_identity import score_layer
from .exceptions import BatchInterruptionRequested
from .field_matching import norm
from .extraction import extract_zip_safely
from .gis_inspector import inspect_all, inspect_dataset
from .partitioning import UF_CODES, normalize_uf, sicar_partition_has_rows
from .sicar_tracking import (
    detect_sicar_uf_from_layer, fingerprint_layer_content, get_fingerprint,
    mark_state_processing, record_fingerprint, hash_file,
)
from .pipeline import process_import
from .prodes_filter import DEFAULT_PRODES_START_YEAR, normalize_prodes_start_year
from .zip_security import run_antivirus, validate_zip, validate_gpkg

logger = logging.getLogger(__name__)

# Versão da política de classificação em lote. Lotes antigos (v1) podem ter
# usado semelhança estrutural genérica para escolher o destino. Eles não devem
# influenciar a nova classificação nem impedir o reprocessamento corretivo.
BATCH_CLASSIFIER_VERSION = 8




def _batch_root_candidates(lote):
    """Retorna todas as raízes seguras conhecidas para um lote.

    A V3.3 não confia em um único volume. O worker procura, nesta ordem:
    1) working canônico atual; 2) caminho salvo no banco; 3) working legado no
    import_inbox; 4) volume legado V3.0–V3.2, quando montado.
    """
    lote_id = int(getattr(lote, 'pk', None) or getattr(lote, 'id', 0) or 0)
    batch_name = f'lote_{lote_id}'
    candidates = []
    trusted_parents = [
        Path(settings.BATCH_DIR).resolve(),
        (Path(settings.IMPORT_INBOX_DIR) / '.manage_batches' / 'working').resolve(),
    ]
    legacy_dir = getattr(settings, 'BATCH_LEGACY_DIR', None)
    if legacy_dir:
        trusted_parents.append(Path(legacy_dir).resolve())

    def normalize(value):
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        try:
            return path.resolve()
        except OSError:
            return path.absolute()

    def add(value, *, require_trusted=True):
        path = normalize(value)
        if path is None or path.name != batch_name:
            return
        if require_trusted and not any(path.parent == parent for parent in trusted_parents):
            return
        if path not in candidates:
            candidates.append(path)

    add(Path(settings.BATCH_DIR) / batch_name)
    add(getattr(lote, 'extracted_path', ''))
    add(Path(settings.IMPORT_INBOX_DIR) / '.manage_batches' / 'working' / batch_name)
    if legacy_dir:
        add(Path(legacy_dir) / batch_name)
    return candidates


def _batch_recovery_roots(lote_id):
    """Áreas de recovery atuais e legadas, sem duplicar caminhos."""
    lote_name = f'lote_{int(lote_id)}'
    parents = [
        Path(settings.BATCH_RECOVERY_DIR),
        Path(settings.BATCH_STORAGE_DIR) / 'recovery',
        Path(settings.IMPORT_INBOX_DIR) / '.manage_batches' / 'recovery',
    ]
    legacy_recovery = getattr(settings, 'BATCH_LEGACY_RECOVERY_DIR', None)
    if legacy_recovery:
        parents.append(Path(legacy_recovery))
    roots = []
    for parent in parents:
        try:
            root = (parent / lote_name).resolve()
        except OSError:
            root = (parent / lote_name).absolute()
        if root not in roots:
            roots.append(root)
    return roots


def _set_batch_root(lote, root):
    root = Path(root).resolve()
    value = str(root)
    if getattr(lote, 'extracted_path', '') != value:
        lote.extracted_path = value
        lote.save(update_fields=['extracted_path'])
    return root


def _existing_batch_root(lote):
    for root in _batch_root_candidates(lote):
        if root.is_dir():
            return _set_batch_root(lote, root)
    return None


def _ensure_batch_root(lote):
    root = _existing_batch_root(lote)
    if root is not None:
        return root
    root = (Path(settings.BATCH_DIR) / f'lote_{lote.pk}').resolve()
    root.mkdir(parents=True, exist_ok=True)
    return _set_batch_root(lote, root)


def _source_slug_from_value(value):
    for slug, enum_value in FONTE_SLUGS.items():
        if str(getattr(enum_value, 'value', enum_value)) == str(value):
            return slug
    return ''


def allowed_input_extensions(source_slug):
    """Extensões aceitas no lote manual de cada fonte.

    A união vem dos perfis técnicos reais já cadastrados; não transforma uma
    fonte em outra nem inventa formatos. A validação específica continua no
    pipeline do dataset antes de qualquer promoção.
    """
    source_slug = str(source_slug or '').strip().lower()
    if source_slug == 'sicar':
        return {'.zip', '.gpkg'}
    specs = datasets_for_source(source_slug)
    allowed = set()
    for spec in specs:
        if spec.data_kind in {'sicor_csv', 'sicor_wkt', 'sicor_gleba_points'}:
            allowed.update({'.gz', '.csv'})
        elif spec.data_kind == 'tabular_flexible':
            allowed.update({'.csv', '.gz', '.zip'})
        elif spec.data_kind == 'spatial_flexible':
            allowed.update({'.zip', '.gpkg', '.geojson', '.json', '.gml', '.kml'})
        else:
            allowed.add('.zip')
    return allowed or {'.zip'}


def _allowed_input_extensions(source_slug):
    return allowed_input_extensions(source_slug)


def _validate_input_extension(filename, source_slug):
    suffix = Path(str(filename or '')).suffix.lower()
    allowed = _allowed_input_extensions(source_slug)
    if suffix not in allowed:
        expected = ', '.join(sorted(allowed))
        raise ValueError(
            f'O arquivo {Path(str(filename or '')).name or "sem nome"} não possui uma extensão permitida '
            f'para esta fonte ({expected}).'
        )
    return suffix


def _validate_input_security(path, source_slug):
    path = Path(path)
    suffix = _validate_input_extension(path.name, source_slug)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError('O arquivo recebido está vazio ou indisponível no storage do lote.')
    if suffix == '.zip':
        return validate_zip(path)
    if suffix == '.gpkg':
        return validate_gpkg(path)
    # CSV/GZIP/GeoJSON/GML/KML recebem validação estrutural no pipeline do
    # dataset. Aqui apenas confirmamos persistência e extensão permitida.
    return {'arquivo': path.name, 'tamanho_bytes': path.stat().st_size, 'validacao_lote': 'OK'}


def _save_batch_upload(uploaded_file, lote_id):
    target = Path(settings.BATCH_DIR) / f'lote_{lote_id}.zip'
    sha = hashlib.sha256()
    size = 0
    with target.open('wb') as dst:
        for chunk in uploaded_file.chunks():
            size += len(chunk)
            sha.update(chunk)
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    return target, sha.hexdigest(), size


def _batch_recovery_root(lote_id):
    # O primeiro root é o recovery canônico independente do working.
    root = _batch_recovery_roots(lote_id)[0]
    root.mkdir(parents=True, exist_ok=True)
    return root


def _create_recovery_link(source_path, lote_id, relative_path):
    """Cria a cópia de recuperação antes de liberar o item ao worker.

    Na V3.3 o recovery fica, por padrão, em um bind mount independente do
    working. Quando hard link é possível usamos a referência barata; em mounts
    diferentes ou no Docker Desktop/Windows fazemos uma cópia real temporária.
    """
    source_path = Path(source_path)
    recovery = _batch_recovery_root(lote_id) / Path(relative_path)
    recovery.parent.mkdir(parents=True, exist_ok=True)
    recovery.unlink(missing_ok=True)
    try:
        os.link(source_path, recovery)
        return recovery
    except OSError as exc:
        # Docker Desktop/Windows e alguns volumes não suportam hard links.
        # Nesse caso fazemos uma cópia real de recuperação. O lote sequencial
        # mantém apenas um arquivo em processamento por vez, então a duplicação
        # é temporária e evita perder uploads de qualquer fonte (.zip/.gz/.csv/etc.).
        try:
            shutil.copy2(source_path, recovery)
            logger.warning(
                'Hard link indisponível para o lote %s; cópia de recuperação criada em %s (%s).',
                lote_id, recovery, exc,
            )
            return recovery
        except OSError as copy_exc:
            recovery.unlink(missing_ok=True)
            raise IOError(
                f'Não foi possível criar a cópia de recuperação do lote {lote_id}: {copy_exc}'
            ) from copy_exc


def _restore_from_recovery(item, root, expected_path):
    recovery = None
    for recovery_root in _batch_recovery_roots(item.lote_id):
        candidate = recovery_root / item.caminho_relativo
        if candidate.is_file():
            recovery = candidate
            break
        if recovery_root.is_dir():
            matches = [value for value in recovery_root.rglob(item.nome_arquivo) if value.is_file()]
            if len(matches) == 1:
                recovery = matches[0]
                break
    if recovery is None:
        return None
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(recovery, expected_path)
    except OSError:
        shutil.copy2(recovery, expected_path)
    if expected_path.is_file():
        item.caminho_relativo = expected_path.relative_to(root).as_posix()
        item.save(update_fields=['caminho_relativo'])
        logger.warning('Arquivo do item %s restaurado pela área de recuperação %s.', item.pk, recovery)
        return expected_path
    return None


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


def create_batch(uploaded_file, source_slug, usuario, default_uf='', prodes_start_year=None):
    fonte = FONTE_SLUGS.get(source_slug)
    if not fonte:
        raise ValueError('Fonte não cadastrada para importação em lote.')

    lote = LoteImportacao.objects.create(
        fonte=fonte,
        nome_arquivo_original=Path(uploaded_file.name).name,
        hash_sha256='0' * 64,
        tamanho_bytes=0,
        administrador=usuario,
        status=LoteImportacao.Status.PREPARANDO,
    )
    batch_zip = None
    extracted = Path(settings.BATCH_DIR) / f'lote_{lote.pk}'
    try:
        if settings.MAX_UPLOAD_SIZE_BYTES and uploaded_file.size > settings.MAX_UPLOAD_SIZE_BYTES:
            raise ValueError('O pacote do lote excede o limite configurado para upload.')

        batch_zip, digest, size = _save_batch_upload(uploaded_file, lote.pk)
        lote.hash_sha256 = digest
        lote.tamanho_bytes = size
        lote.quarantine_path = str(batch_zip.relative_to(settings.BASE_DIR))
        lote.save(update_fields=['hash_sha256','tamanho_bytes','quarantine_path'])

        security = validate_zip(batch_zip)
        antivirus = run_antivirus(batch_zip)
        extract_zip_safely(batch_zip, extracted)
        lote.extracted_path = str(extracted.resolve())

        inner_archives = sorted(p for p in extracted.rglob('*.zip') if p.is_file())
        if not inner_archives:
            raise ValueError(
                'Nenhum ZIP de dataset foi encontrado dentro do lote. '
                'O lote deve conter os arquivos ZIP oficiais sem descompactá-los.'
            )

        review_count = 0
        for archive in inner_archives:
            relative = archive.relative_to(extracted).as_posix()
            _create_recovery_link(archive, lote.pk, relative)
            ItemLoteImportacao.objects.create(
                lote=lote,
                caminho_relativo=relative,
                nome_arquivo=archive.name,
                uf=(normalize_uf(default_uf) or _detect_uf_hint(relative)) if source_slug == 'sicar' else '',
                hash_sha256=hash_file(archive),
                progresso=0,
                etapa='Aguardando na fila',
                status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
                motivo='',
            )

        lote.status = LoteImportacao.Status.ANALISANDO if source_slug == 'sicar' else LoteImportacao.Status.PROCESSANDO
        lote.data_finalizacao = None
        lote.resultado = {
            'fase': 'ANALISE' if source_slug == 'sicar' else 'IMPORTACAO',
            'seguranca_zip_lote': security,
            'antimalware_lote': antivirus,
            'arquivos_zip_encontrados': len(inner_archives),
            'itens_em_revisao_inicial': 0,
            'filtros': ({
                'ano_inicial': normalize_prodes_start_year(prodes_start_year),
            } if source_slug == 'prodes' else {}),
        }
        lote.save(update_fields=['status','data_finalizacao','resultado','extracted_path'])
        registrar_auditoria(
            usuario,
            'LOTE_IMPORTACAO_CRIADO',
            'LoteImportacao',
            lote.pk,
            {'fonte': str(fonte), 'arquivos': len(inner_archives), 'hash_sha256': digest},
        )
        return lote
    except Exception as exc:
        logger.exception('Falha ao preparar lote de importação %s', lote.pk)
        lote.status = LoteImportacao.Status.FALHOU
        lote.motivo_falha = str(exc)
        lote.data_finalizacao = timezone.now()
        lote.save(update_fields=['status','motivo_falha','data_finalizacao','extracted_path'])
        registrar_auditoria(
            usuario, 'LOTE_IMPORTACAO_FALHOU', 'LoteImportacao', lote.pk, {'motivo': str(exc)}
        )
        if extracted.exists():
            shutil.rmtree(extracted, ignore_errors=True)
        if batch_zip and Path(batch_zip).exists():
            Path(batch_zip).unlink(missing_ok=True)
        for recovery_root in _batch_recovery_roots(lote.pk):
            if recovery_root.exists():
                shutil.rmtree(recovery_root, ignore_errors=True)
        return lote


def create_batch_from_uploads(uploaded_files, source_slug, usuario, default_uf='', prodes_start_year=None):
    fonte = FONTE_SLUGS.get(source_slug)
    if not fonte:
        raise ValueError('Fonte não cadastrada para importação em lote.')
    files = list(uploaded_files or [])
    if not files:
        raise ValueError('Nenhum arquivo foi selecionado para o lote.')

    lote = LoteImportacao.objects.create(
        fonte=fonte, nome_arquivo_original=f'{len(files)} arquivo(s) selecionado(s)',
        hash_sha256='0' * 64, tamanho_bytes=0, administrador=usuario,
        status=LoteImportacao.Status.PREPARANDO,
    )
    extracted = Path(settings.BATCH_DIR) / f'lote_{lote.pk}'
    extracted.mkdir(parents=True, exist_ok=True)
    manifest = hashlib.sha256()
    total = 0
    try:
        for index, uploaded in enumerate(files, 1):
            _validate_input_extension(uploaded.name, source_slug)
            if settings.MAX_UPLOAD_SIZE_BYTES and uploaded.size > settings.MAX_UPLOAD_SIZE_BYTES:
                raise ValueError(f'O arquivo {uploaded.name} excede o limite configurado para upload.')
            safe_name = Path(uploaded.name).name
            item_dir = extracted / f'item_{index:04d}'
            item_dir.mkdir(parents=True, exist_ok=True)
            target = item_dir / safe_name
            file_hash = hashlib.sha256()
            with target.open('wb') as dst:
                for chunk in uploaded.chunks():
                    dst.write(chunk)
                    file_hash.update(chunk)
                    total += len(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            if not target.is_file():
                raise IOError(f'O arquivo {safe_name} não permaneceu disponível na área compartilhada do lote.')
            expected_size = int(getattr(uploaded, 'size', 0) or 0)
            if expected_size and target.stat().st_size != expected_size:
                raise IOError(
                    f'O arquivo {safe_name} foi gravado com tamanho divergente '
                    f'({target.stat().st_size} de {expected_size} bytes). O lote foi bloqueado.'
                )
            manifest.update(safe_name.encode('utf-8', errors='replace'))
            manifest.update(b'\0')
            manifest.update(file_hash.digest())
            relative = target.relative_to(extracted).as_posix()
            _create_recovery_link(target, lote.pk, relative)
            ItemLoteImportacao.objects.create(
                lote=lote, caminho_relativo=relative,
                nome_arquivo=safe_name,
                uf=(normalize_uf(default_uf) or _detect_uf_hint(safe_name)) if source_slug == 'sicar' else '',
                hash_sha256=file_hash.hexdigest(),
                progresso=0, etapa='Aguardando na fila',
                status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
            )

        lote.hash_sha256 = manifest.hexdigest()
        lote.tamanho_bytes = total
        lote.extracted_path = str(extracted.resolve())
        lote.status = LoteImportacao.Status.ANALISANDO if source_slug == 'sicar' else LoteImportacao.Status.PROCESSANDO
        lote.resultado = {
            'fase': 'ANALISE' if source_slug == 'sicar' else 'IMPORTACAO',
            'modo': 'MULTIPLOS_ARQUIVOS',
            'arquivos_encontrados': len(files),
            'arquivos_zip_encontrados': sum(1 for value in files if Path(value.name).suffix.lower() == '.zip'),
            'arquivos_gpkg_encontrados': sum(1 for value in files if Path(value.name).suffix.lower() == '.gpkg'),
            'filtros': ({
                'ano_inicial': normalize_prodes_start_year(prodes_start_year),
            } if source_slug == 'prodes' else {}),
        }
        lote.save(update_fields=['hash_sha256','tamanho_bytes','extracted_path','status','resultado'])
        registrar_auditoria(
            usuario, 'LOTE_IMPORTACAO_CRIADO', 'LoteImportacao', lote.pk,
            {'fonte': str(fonte), 'arquivos': len(files), 'modo': 'MULTIPLOS_ARQUIVOS'},
        )
        return lote
    except Exception as exc:
        logger.exception('Falha ao preparar lote de múltiplos arquivos %s', lote.pk)
        lote.status = LoteImportacao.Status.FALHOU
        lote.motivo_falha = str(exc)
        lote.data_finalizacao = timezone.now()
        lote.save(update_fields=['status','motivo_falha','data_finalizacao'])
        shutil.rmtree(extracted, ignore_errors=True)
        for recovery_root in _batch_recovery_roots(lote.pk):
            if recovery_root.exists():
                shutil.rmtree(recovery_root, ignore_errors=True)
        return lote



def create_sequential_batch(source_slug, usuario, expected_files, default_uf='', prodes_start_year=None, filenames=None):
    """Cria o lote lógico sem receber todos os arquivos de uma vez.

    O navegador envia um arquivo, aguarda o worker terminar aquele item e só
    então envia o próximo. Nenhum item é criado antes de o arquivo correspondente
    estar completamente persistido no volume compartilhado.
    """
    fonte = FONTE_SLUGS.get(source_slug)
    if not fonte:
        raise ValueError('Fonte não cadastrada para importação em lote.')
    expected = int(expected_files or 0)
    if expected < 1:
        raise ValueError('O lote sequencial precisa conter pelo menos um arquivo.')
    if expected > 500:
        raise ValueError('O lote excede o limite administrativo de 500 arquivos por seleção.')

    names = [Path(str(value)).name for value in (filenames or [])][:expected]
    lote = LoteImportacao.objects.create(
        fonte=fonte,
        nome_arquivo_original=f'{expected} arquivo(s) — envio sequencial',
        hash_sha256='0' * 64,
        tamanho_bytes=0,
        administrador=usuario,
        status=LoteImportacao.Status.PREPARANDO,
        extracted_path=str((Path(settings.BATCH_DIR) / 'pending').as_posix()),
        resultado={
            'fase': 'ANALISE' if source_slug == 'sicar' else 'IMPORTACAO',
            'modo': 'UPLOAD_SEQUENCIAL',
            'sequencial_finalizado': False,
            'arquivos_esperados': expected,
            'arquivos_recebidos': 0,
            'nomes_selecionados': names,
            'filtros': ({
                'ano_inicial': normalize_prodes_start_year(prodes_start_year),
            } if source_slug == 'prodes' else {}),
            'uf_padrao': normalize_uf(default_uf) if source_slug == 'sicar' else '',
        },
    )
    root = Path(settings.BATCH_DIR) / f'lote_{lote.pk}'
    root.mkdir(parents=True, exist_ok=True)
    lote.extracted_path = str(root)
    lote.save(update_fields=['extracted_path'])
    registrar_auditoria(
        usuario, 'LOTE_SEQUENCIAL_INICIADO', 'LoteImportacao', lote.pk,
        {'fonte': str(fonte), 'arquivos_esperados': expected},
    )
    return lote


def append_sequential_upload(lote_id, uploaded_file, usuario, index=None):
    """Persiste exatamente um arquivo e só depois o libera para o worker."""
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        result = dict(lote.resultado or {})
        if result.get('modo') != 'UPLOAD_SEQUENCIAL':
            raise ValueError('Este lote não utiliza envio sequencial.')
        if result.get('sequencial_finalizado'):
            raise ValueError('O envio deste lote já foi finalizado.')
        if lote.administrador_id != usuario.id and not usuario.is_superuser:
            raise PermissionError('O usuário não possui permissão para continuar este lote.')
        if lote.itens.filter(status__in=[
            ItemLoteImportacao.Status.AGUARDANDO_FILA,
            ItemLoteImportacao.Status.PENDENTE,
            ItemLoteImportacao.Status.PROCESSANDO,
        ]).exists():
            raise ValueError('Aguarde o arquivo atual terminar antes de enviar o próximo.')

        received = int(result.get('arquivos_recebidos') or 0)
        expected = int(result.get('arquivos_esperados') or 0)
        next_index = received + 1
        requested_index = int(index or next_index)
        if requested_index != next_index:
            raise ValueError(f'O próximo arquivo esperado é o item {next_index}.')
        if next_index > expected:
            raise ValueError('Todos os arquivos previstos neste lote já foram recebidos.')
        source_slug = _source_slug_from_value(lote.fonte)
        _validate_input_extension(uploaded_file.name, source_slug)
        if settings.MAX_UPLOAD_SIZE_BYTES and uploaded_file.size > settings.MAX_UPLOAD_SIZE_BYTES:
            raise ValueError(f'O arquivo {uploaded_file.name} excede o limite configurado para upload.')

        root = _ensure_batch_root(lote)
        safe_name = Path(uploaded_file.name).name
        item_dir = root / f'item_{next_index:04d}'
        item_dir.mkdir(parents=True, exist_ok=True)
        target = item_dir / safe_name
        digest = hashlib.sha256()
        size = 0
        with target.open('wb') as dst:
            for chunk in uploaded_file.chunks():
                dst.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if not target.is_file() or target.stat().st_size != size:
            raise IOError(f'O arquivo {safe_name} não foi persistido integralmente no storage do lote.')

        relative = target.relative_to(root).as_posix()
        _create_recovery_link(target, lote.pk, relative)
        pre_dataset_slug = ''
        pre_dataset_label = ''
        pre_spec, _pre_report = _preclassify_input_name(source_slug, target)
        if pre_spec is not None:
            pre_dataset_slug = pre_spec.slug
            pre_dataset_label = pre_spec.label

        item = ItemLoteImportacao.objects.create(
            lote=lote,
            caminho_relativo=relative,
            nome_arquivo=safe_name,
            uf=(normalize_uf(result.get('uf_padrao')) or _detect_uf_hint(safe_name)) if source_slug == 'sicar' else '',
            dataset_slug=pre_dataset_slug,
            dataset_label=pre_dataset_label,
            hash_sha256=digest.hexdigest(),
            progresso=0,
            etapa='Aguardando na fila',
            status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
            motivo='',
        )
        manifest = hashlib.sha256()
        manifest.update((lote.hash_sha256 or '').encode('ascii', errors='ignore'))
        manifest.update(digest.digest())
        result['arquivos_recebidos'] = next_index
        result['arquivo_atual'] = safe_name
        result['sequencial_aguardando_upload'] = False
        lote.hash_sha256 = manifest.hexdigest()
        lote.tamanho_bytes = int(lote.tamanho_bytes or 0) + size
        lote.resultado = result
        lote.status = LoteImportacao.Status.ANALISANDO if source_slug == 'sicar' else LoteImportacao.Status.PROCESSANDO
        lote.data_finalizacao = None
        lote.save(update_fields=['hash_sha256', 'tamanho_bytes', 'resultado', 'status', 'data_finalizacao'])

    registrar_auditoria(
        usuario, 'LOTE_SEQUENCIAL_ARQUIVO_RECEBIDO', 'ItemLoteImportacao', item.pk,
        {'lote_id': lote.pk, 'indice': next_index, 'arquivo': safe_name, 'bytes': size},
    )
    return item


def finalize_sequential_batch(lote_id, usuario):
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        result = dict(lote.resultado or {})
        if result.get('modo') != 'UPLOAD_SEQUENCIAL':
            raise ValueError('Este lote não utiliza envio sequencial.')
        expected = int(result.get('arquivos_esperados') or 0)
        received = int(result.get('arquivos_recebidos') or 0)
        if received != expected:
            raise ValueError(f'O lote recebeu {received} de {expected} arquivo(s).')
        if lote.itens.filter(status__in=[
            ItemLoteImportacao.Status.AGUARDANDO_FILA,
            ItemLoteImportacao.Status.PENDENTE,
            ItemLoteImportacao.Status.PROCESSANDO,
        ]).exists():
            raise ValueError('O último arquivo ainda está sendo processado.')
        result['sequencial_finalizado'] = True
        result['sequencial_aguardando_upload'] = False
        result['envio_finalizado_em'] = timezone.now().isoformat()
        lote.resultado = result
        lote.save(update_fields=['resultado'])
    registrar_auditoria(
        usuario, 'LOTE_SEQUENCIAL_FINALIZADO', 'LoteImportacao', lote.pk,
        {'arquivos_recebidos': received},
    )
    return update_batch_status(lote.pk)


def _cleanup_finished_item_file(item):
    """Libera disco após sucesso real de um item sequencial.

    Falha, revisão e SICAR ainda em PRONTO_IMPORTAR preservam o ZIP. Se o
    filesystem impedir a limpeza, o próximo upload não é liberado silenciosamente:
    a tela recebe "limpeza pendente" e interrompe a sequência.
    """
    if (item.lote.resultado or {}).get('modo') != 'UPLOAD_SEQUENCIAL':
        return True
    if item.status not in {
        ItemLoteImportacao.Status.CONCLUIDO,
        ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
        ItemLoteImportacao.Status.SEM_ALTERACAO,
        ItemLoteImportacao.Status.INTERROMPIDO,
    }:
        return True
    root = _existing_batch_root(item.lote)
    if root is None:
        # Se working já foi removido, não convertemos um processamento bem-sucedido
        # em falha de limpeza. Recovery é limpo abaixo quando existir.
        root = (Path(settings.BATCH_DIR) / f'lote_{item.lote_id}').resolve()
    path = root / item.caminho_relativo
    try:
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
        for recovery_root in _batch_recovery_roots(item.lote_id):
            recovery = recovery_root / item.caminho_relativo
            recovery.unlink(missing_ok=True)
            try:
                recovery.parent.rmdir()
            except OSError:
                pass
    except OSError as exc:
        logger.exception('Falha ao liberar temporário do item %s após importação concluída.', item.pk)
        item.etapa = 'Concluído — limpeza temporária pendente'
        item.motivo = (
            (item.motivo + ' ' if item.motivo else '')
            + f'Os dados foram processados, mas o arquivo temporário não pôde ser removido: {exc}'
        )[:4000]
        item.save(update_fields=['etapa', 'motivo'])
        return False
    if item.status == ItemLoteImportacao.Status.INTERROMPIDO:
        item.etapa = 'Interrompido — temporário liberado'
    elif item.status in {ItemLoteImportacao.Status.SEM_ALTERACAO, ItemLoteImportacao.Status.IGNORADO_DUPLICADO}:
        item.etapa = 'Sem alteração — temporário liberado'
    else:
        item.etapa = 'Concluído — temporário liberado'
    item.save(update_fields=['etapa'])
    return True


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



def _manifest_hash(paths, base_dir):
    digest = hashlib.sha256()
    total = 0
    for path in sorted(paths, key=lambda p: p.as_posix().lower()):
        relative = path.relative_to(base_dir).as_posix()
        digest.update(relative.encode('utf-8'))
        digest.update(b'\0')
        file_hash = hashlib.sha256()
        with path.open('rb') as src:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                file_hash.update(chunk)
        digest.update(file_hash.digest())
    return digest.hexdigest(), total


def _source_folder(source_slug):
    desired = {
        'sicar': 'SICAR',
        'ibama': 'IBAMA',
        'icmbio': 'ICMBIO',
        'cnuc': 'CNUC',
        'prodes': 'PRODES',
        'incra': 'INCRA',
    }[source_slug]
    root = Path(settings.IMPORT_INBOX_DIR)
    try:
        children = list(root.iterdir()) if root.exists() else []
    except OSError as exc:
        logger.warning('import_inbox temporariamente indisponível (%s): %s', root, exc)
        return root / desired
    for child in children:
        if child.is_dir() and child.name.upper() == desired:
            return child
    return root / desired


def _folder_already_claimed(folder):
    return any(folder.glob('PROCESSANDO_CONFRONTA_*.txt'))


def _claim_folder_marker(folder, lote_id):
    ready = folder / 'PRONTO.txt'
    claimed = folder / f'PROCESSANDO_CONFRONTA_{lote_id}.txt'
    if ready.exists():
        ready.replace(claimed)
    (folder / 'ERRO_PREPARACAO_CONFRONTA.txt').unlink(missing_ok=True)
    return claimed


def _mark_folder_preparation_error(folder, message):
    ready = folder / 'PRONTO.txt'
    if ready.exists():
        ready.unlink(missing_ok=True)
    marker = folder / 'ERRO_PREPARACAO_CONFRONTA.txt'
    marker.write_text(str(message), encoding='utf-8')


def create_batch_from_folder(folder, source_slug, usuario):
    folder = Path(folder).resolve()
    root = Path(settings.IMPORT_INBOX_DIR).resolve()
    if folder != root and root not in folder.parents:
        raise ValueError('Pasta de lote fora da caixa de entrada autorizada.')
    fonte = FONTE_SLUGS.get(source_slug)
    if not fonte:
        raise ValueError('Fonte não cadastrada para pasta monitorada.')
    allowed = _allowed_input_extensions(source_slug)
    archives = sorted(
        p for p in folder.rglob('*')
        if p.is_file() and p.suffix.lower() in allowed
    )
    if not archives:
        expected = 'ZIP ou GPKG' if source_slug == 'sicar' else 'ZIP'
        raise ValueError(f'Nenhum arquivo {expected} encontrado na pasta marcada como PRONTO.')
    digest, total = _manifest_hash(archives, folder)
    relative_folder = folder.relative_to(Path(settings.BASE_DIR)).as_posix() if Path(settings.BASE_DIR).resolve() in folder.parents else str(folder)
    with transaction.atomic():
        lote = LoteImportacao.objects.create(
            fonte=fonte,
            nome_arquivo_original=f'Pasta monitorada: {folder.relative_to(root).as_posix()}',
            hash_sha256=digest,
            tamanho_bytes=total,
            administrador=usuario,
            status=(LoteImportacao.Status.ANALISANDO if source_slug == 'sicar' else LoteImportacao.Status.PROCESSANDO),
            extracted_path=relative_folder,
            resultado={
                'fase': ('ANALISE' if source_slug == 'sicar' else 'IMPORTACAO'),
                'modo': 'PASTA_MONITORADA',
                'arquivos_encontrados': len(archives),
                'arquivos_zip_encontrados': sum(1 for value in archives if value.suffix.lower() == '.zip'),
                'arquivos_gpkg_encontrados': sum(1 for value in archives if value.suffix.lower() == '.gpkg'),
                'filtros': ({
                    'ano_inicial': DEFAULT_PRODES_START_YEAR,
                } if source_slug == 'prodes' else {}),
            },
        )
        for archive in archives:
            relative = archive.relative_to(folder).as_posix()
            ItemLoteImportacao.objects.create(
                lote=lote,
                caminho_relativo=relative,
                nome_arquivo=archive.name,
                uf=_detect_uf_hint(relative) if source_slug == 'sicar' else '',
                hash_sha256=hash_file(archive),
                progresso=0, etapa='Aguardando na fila',
                status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
            )
    try:
        _claim_folder_marker(folder, lote.pk)
    except Exception:
        lote.delete()
        raise
    registrar_auditoria(
        usuario, 'LOTE_PASTA_MONITORADA_CRIADO', 'LoteImportacao', lote.pk,
        {'fonte': str(fonte), 'pasta': str(folder), 'arquivos': len(archives)},
    )
    return lote


def scan_inbox_once(usuario=None):
    """Descobre pastas marcadas com PRONTO.txt sem derrubar o worker se o bind mount oscilar.

    Uploads do painel não dependem desta pasta; eles usam BATCH_STORAGE_DIR em
    volume Docker próprio. Assim uma indisponibilidade temporária do
    import_inbox apenas adia o scanner externo e nunca interrompe a fila web.
    """
    if usuario is None:
        from administracao.models import User
        configured_email = os.getenv('DJANGO_SUPERUSER_EMAIL', '').strip().lower()
        usuario = User.objects.filter(email=configured_email, is_active=True).first() if configured_email else None
        if usuario is None:
            usuario = User.objects.filter(is_superuser=True, is_active=True).order_by('id').first()
        if usuario is None:
            usuario = User.objects.filter(is_staff=True, is_active=True).order_by('id').first()
    if usuario is None:
        logger.warning('Pasta monitorada ignorada: não há administrador ativo para auditoria.')
        return []

    created = []
    for source_slug in BATCH_FONTE_SLUGS:
        try:
            source = _source_folder(source_slug)
            if not source.exists():
                continue
            if (source / 'PRONTO.txt').exists() and not _folder_already_claimed(source):
                try:
                    created.append(create_batch_from_folder(source, source_slug, usuario))
                except Exception as exc:
                    logger.exception('Falha ao criar lote monitorado em %s', source)
                    _mark_folder_preparation_error(source, exc)
            elif source_slug == 'sicar':
                try:
                    state_dirs = sorted(p for p in source.iterdir() if p.is_dir())
                except OSError as exc:
                    logger.warning('Scanner SICAR adiou leitura de %s: %s', source, exc)
                    continue
                for state_dir in state_dirs:
                    if not (state_dir / 'PRONTO.txt').exists() or _folder_already_claimed(state_dir):
                        continue
                    try:
                        created.append(create_batch_from_folder(state_dir, source_slug, usuario))
                    except Exception as exc:
                        logger.exception('Falha ao criar lote monitorado SICAR em %s', state_dir)
                        _mark_folder_preparation_error(state_dir, exc)
        except OSError as exc:
            logger.warning('Scanner adiou a fonte %s por indisponibilidade do filesystem: %s', source_slug, exc)
            continue
    return created


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


def _item_archive_path(item):
    lote = item.lote

    # Primeiro tenta todas as raízes conhecidas do lote. Isso cobre upgrades de
    # storage e evita falha falsa quando ``extracted_path`` antigo ficou salvo
    # no banco, mas o arquivo está no volume canônico atual.
    for root in _batch_root_candidates(lote):
        if not root.is_dir():
            continue
        path = (root / item.caminho_relativo).resolve()
        if path != root and root not in path.parents:
            raise ValueError('Caminho do item do lote saiu da área autorizada.')
        if path.is_file():
            _set_batch_root(lote, root)
            return path

        matches = [candidate.resolve() for candidate in root.rglob(item.nome_arquivo) if candidate.is_file()]
        if len(matches) == 1:
            recovered = matches[0]
            item.caminho_relativo = recovered.relative_to(root).as_posix()
            item.save(update_fields=['caminho_relativo'])
            _set_batch_root(lote, root)
            logger.warning(
                'Caminho físico do item %s do lote %s recuperado para %s.',
                item.pk, item.lote_id, item.caminho_relativo,
            )
            return recovered

    # A raiz de working pode ter sido removida ou o lote pode vir de uma versão
    # antiga. Tentamos todas as áreas de recovery (canônica + legadas) e, se houver
    # uma cópia válida, recriamos o working atual antes de processar.
    root = _ensure_batch_root(lote)
    expected_path = (root / item.caminho_relativo).resolve()
    if expected_path != root and root not in expected_path.parents:
        raise ValueError('Caminho do item do lote saiu da área autorizada.')
    restored = _restore_from_recovery(item, root, expected_path)
    if restored is not None:
        logger.warning(
            'Área working do lote %s foi recriada a partir da recuperação para o item %s.',
            lote.pk, item.pk,
        )
        return restored

    roots = ', '.join(str(root) for root in _batch_root_candidates(lote))
    recovery_roots = ', '.join(str(root) for root in _batch_recovery_roots(item.lote_id))
    raise FileNotFoundError(
        f'O arquivo persistente do lote não foi localizado. Working verificado: {roots}. '
        f'Recovery verificado: {recovery_roots}. Reenvie somente este arquivo; '
        'nenhum dado será promovido parcialmente.'
    )



def _batch_interruption_requested(lote_id):
    lote = LoteImportacao.objects.filter(pk=lote_id).values('status', 'resultado').first()
    if not lote:
        return True
    result = lote.get('resultado') or {}
    return bool(
        result.get('interrupcao_solicitada')
        or lote.get('status') in {LoteImportacao.Status.INTERROMPENDO, LoteImportacao.Status.INTERROMPIDO}
    )


def request_batch_interruption(lote_id, usuario):
    """Solicita interrupção cooperativa sem matar o worker nem tocar na base ativa.

    Itens ainda não iniciados são encerrados imediatamente. Um item já em
    processamento será interrompido no próximo checkpoint anterior à promoção
    atômica. Se a publicação já tiver começado, ela termina com segurança e o
    lote para antes de qualquer item seguinte.
    """
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        if not lote.pode_interromper:
            raise ValueError('Este lote já está finalizado e não pode ser interrompido.')

        now = timezone.now()
        result = dict(lote.resultado or {})
        result['interrupcao_solicitada'] = True
        result['interrupcao_solicitada_em'] = now.isoformat()
        result['interrupcao_solicitada_por'] = getattr(usuario, 'email', '') or str(getattr(usuario, 'pk', ''))
        lote.resultado = result

        waiting = lote.itens.filter(status__in=[
            ItemLoteImportacao.Status.AGUARDANDO_FILA,
            ItemLoteImportacao.Status.PENDENTE,
            ItemLoteImportacao.Status.PRONTO_IMPORTAR,
        ])
        waiting.update(
            status=ItemLoteImportacao.Status.INTERROMPIDO,
            etapa='Interrompido pelo administrador',
            motivo='O lote foi interrompido antes deste arquivo iniciar a promoção.',
            finalizado_em=now,
        )
        active = lote.itens.filter(status=ItemLoteImportacao.Status.PROCESSANDO).exists()
        lote.status = LoteImportacao.Status.INTERROMPENDO if active else LoteImportacao.Status.INTERROMPIDO
        lote.data_finalizacao = None if active else now
        lote.save(update_fields=['resultado', 'status', 'data_finalizacao'])

    registrar_auditoria(
        usuario, 'LOTE_INTERRUPCAO_SOLICITADA', 'LoteImportacao', lote.pk,
        {'fonte': str(lote.fonte), 'item_em_processamento': active},
    )
    if not active:
        _cleanup_finished_batch_files(lote)
    return lote


def delete_batch_record(lote_id, usuario):
    """Remove o lote da fila/painel sem apagar dados publicados.

    A remoção é lógica (soft delete) para que um worker que ainda esteja
    encerrando uma etapa possa perceber a solicitação de interrupção sem
    perder as referências do lote/item. Itens não iniciados são interrompidos
    imediatamente. Um item já em execução termina/aborta no próximo checkpoint
    seguro do pipeline. Nenhuma tabela operacional/RAW já publicada é apagada.
    """
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        if lote.oculto_painel:
            return lote.pk

        now = timezone.now()
        result = dict(lote.resultado or {})
        result['interrupcao_solicitada'] = True
        result.setdefault('interrupcao_solicitada_em', now.isoformat())
        result.setdefault(
            'interrupcao_solicitada_por',
            getattr(usuario, 'email', '') or str(getattr(usuario, 'pk', '')),
        )
        result['removido_da_fila'] = True
        result['removido_da_fila_em'] = now.isoformat()
        result['removido_da_fila_por'] = getattr(usuario, 'email', '') or str(getattr(usuario, 'pk', ''))
        lote.resultado = result

        waiting = lote.itens.filter(status__in=[
            ItemLoteImportacao.Status.AGUARDANDO_FILA,
            ItemLoteImportacao.Status.PENDENTE,
            ItemLoteImportacao.Status.PRONTO_IMPORTAR,
        ])
        waiting.update(
            status=ItemLoteImportacao.Status.INTERROMPIDO,
            etapa='Removido da fila pelo administrador',
            motivo='O lote foi removido da fila antes deste arquivo iniciar.',
            finalizado_em=now,
        )

        active = lote.itens.filter(status=ItemLoteImportacao.Status.PROCESSANDO).exists()
        previous_status = lote.status
        if active:
            lote.status = LoteImportacao.Status.INTERROMPENDO
            lote.data_finalizacao = None
        elif lote.status in {
            LoteImportacao.Status.RECEBIDO,
            LoteImportacao.Status.PREPARANDO,
            LoteImportacao.Status.ANALISANDO,
            LoteImportacao.Status.AGUARDANDO_CONFIRMACAO,
            LoteImportacao.Status.PROCESSANDO,
            LoteImportacao.Status.INTERROMPENDO,
        }:
            lote.status = LoteImportacao.Status.INTERROMPIDO
            lote.data_finalizacao = now

        lote.oculto_painel = True
        lote.removido_painel_em = now
        lote.save(update_fields=[
            'resultado', 'status', 'data_finalizacao', 'oculto_painel', 'removido_painel_em'
        ])
        lote_pk = lote.pk
        fonte = str(lote.fonte)

    # Não remover arquivos enquanto um item ainda estiver efetivamente em uso.
    # O worker fará a limpeza ao terminar/interromper no checkpoint seguro.
    if not active:
        _cleanup_batch_paths(lote)

    registrar_auditoria(
        usuario, 'LOTE_REMOVIDO_DA_FILA', 'LoteImportacao', lote_pk,
        {
            'fonte': fonte,
            'status_anterior': previous_status,
            'item_em_processamento': active,
            'dados_publicados_preservados': True,
            'remocao_logica': True,
        },
    )
    return lote_pk

def _cleanup_batch_paths(lote):
    if (lote.resultado or {}).get('modo') != 'PASTA_MONITORADA':
        # Remove somente as raízes conhecidas e confinadas ao lote. Isso também
        # limpa resíduos de versões antigas sem depender de extracted_path único.
        for path in _batch_root_candidates(lote):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
    if lote.quarantine_path:
        path = Path(lote.quarantine_path)
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        if path.exists() and path.is_file():
            path.unlink(missing_ok=True)
    for recovery_root in _batch_recovery_roots(lote.pk):
        if recovery_root.exists():
            shutil.rmtree(recovery_root, ignore_errors=True)


def claim_next_item():
    with transaction.atomic():
        item = (
            ItemLoteImportacao.objects.select_for_update(skip_locked=True)
            .select_related('lote','lote__administrador')
            .filter(
                status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
                lote__status__in=[LoteImportacao.Status.ANALISANDO, LoteImportacao.Status.PROCESSANDO],
            )
            .order_by('id')
            .first()
        )
        if not item:
            return None
        item.status = ItemLoteImportacao.Status.PROCESSANDO
        item.iniciado_em = timezone.now()
        item.progresso = 3
        item.etapa = 'Iniciando análise'
        item.save(update_fields=['status','iniciado_em','progresso','etapa'])
        return item


def _set_item_progress(item_id, percent, stage):
    percent = max(0, min(100, int(percent)))
    ItemLoteImportacao.objects.filter(pk=item_id).update(progresso=percent, etapa=str(stage)[:160])


def _register_unchanged_import(item, spec, classification, uf, archive):
    """Registra a verificação sem executar GDAL/PostGIS novamente."""
    now = timezone.now()
    imp = Importacao.objects.create(
        fonte=spec.fonte,
        dataset_slug=spec.slug,
        dataset_label=spec.label,
        nome_arquivo_original=archive.name,
        hash_sha256=item.hash_sha256 or hash_file(archive),
        tamanho_bytes=archive.stat().st_size,
        administrador=item.lote.administrador,
        status=Importacao.Status.SEM_ALTERACAO,
        identidade_status='CONFIRMADO',
        data_finalizacao=now,
        resultado={
            'sem_alteracao': True,
            'motivo': 'Fingerprint do conteúdo idêntico à última versão confirmada para esta UF e dataset.',
            'contexto': {
                'lote_id': item.lote_id,
                'caminho_lote': item.caminho_relativo,
                'uf': uf,
                'batch_classifier_version': BATCH_CLASSIFIER_VERSION,
                'batch_classification': classification,
            },
            'fingerprint_conteudo': item.fingerprint_conteudo,
        },
        contexto={
            'lote_id': item.lote_id,
            'caminho_lote': item.caminho_relativo,
            'uf': uf,
            'batch_classifier_version': BATCH_CLASSIFIER_VERSION,
            'batch_classification': classification,
        },
    )
    registrar_auditoria(
        item.lote.administrador,
        'SICAR_VERIFICADO_SEM_ALTERACAO',
        'Importacao',
        imp.pk,
        {'uf': uf, 'dataset': spec.slug, 'fingerprint': item.fingerprint_conteudo},
    )
    return imp


def _finalize_sicar_fingerprint(item, spec, imp, changed):
    if not item.uf or not item.fingerprint_conteudo:
        return
    record_fingerprint(
        item.uf,
        spec.slug,
        item.fingerprint_conteudo,
        item.hash_sha256,
        imp,
        changed=changed,
    )


def _finish_item(item, status, stage, motivo='', *, importacao=None, progress=100):
    item.status = status
    item.etapa = stage
    item.motivo = motivo
    item.progresso = max(0, min(100, int(progress)))
    item.finalizado_em = timezone.now()
    if importacao is not None:
        item.importacao = importacao
    fields = ['status', 'etapa', 'motivo', 'progresso', 'finalizado_em']
    if importacao is not None:
        fields.append('importacao')
    item.save(update_fields=fields)
    return item


def _resolve_sicar_uf(item, classification):
    """Resolve a UF administrativa sem exigir exclusividade territorial no arquivo.

    A UF do lote/painel organiza qual snapshot estadual está sendo atualizado.
    Arquivos SICAR de regiões de divisa podem trazer COD_IMOVEL de UFs vizinhas;
    isso é válido desde que a UF administrativa também esteja presente.
    """
    report = classification.get('sicar_uf') or {}
    detected = sorted({
        normalize_uf(value) for value in report.get('detectadas', []) if normalize_uf(value)
    })
    auto_uf = normalize_uf(report.get('uf'))
    selected_uf = normalize_uf(item.uf)
    hint_uf = _detect_uf_hint(item.caminho_relativo) or _detect_uf_hint(item.nome_arquivo)

    # Preferimos a escolha explícita do administrador. A dica do caminho/nome só
    # entra quando não existe seleção explícita. Em ambos os casos, se a amostra
    # conseguiu identificar UFs, a UF administrativa precisa aparecer nela.
    administrative_uf = selected_uf or hint_uf
    if administrative_uf and detected:
        if administrative_uf not in detected:
            return '', (
                f'A UF administrativa {administrative_uf} não foi encontrada no conteúdo amostrado '
                f'({", ".join(detected)}). O arquivo foi mantido para revisão e nada foi promovido.'
            )
        return administrative_uf, ''

    if administrative_uf:
        # A amostra pode ser inconclusiva; o pipeline confirma integralmente o
        # staging antes da promoção e bloqueará a carga se a UF não estiver lá.
        return administrative_uf, ''

    if auto_uf:
        return auto_uf, ''

    if len(detected) == 1:
        return detected[0], ''

    if len(detected) > 1:
        return '', (
            'Foram detectadas várias UFs no conteúdo (' + ', '.join(detected) + '). '
            'Selecione a UF administrativa deste lote para indicar qual snapshot estadual '
            'está sendo atualizado. As UFs vizinhas serão aceitas como registros de divisa.'
        )

    return '', (
        'A UF não pôde ser identificada com segurança. Selecione o estado no relatório '
        'do lote e reprocesse o item.'
    )


def _same_partition_item(lote, item, uf, dataset_slug):
    return (
        ItemLoteImportacao.objects.filter(
            lote=lote,
            uf=uf,
            dataset_slug=dataset_slug,
            id__lt=item.id,
            status__in=[
                ItemLoteImportacao.Status.PRONTO_IMPORTAR,
                ItemLoteImportacao.Status.CONCLUIDO,
                ItemLoteImportacao.Status.SEM_ALTERACAO,
                ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
            ],
        )
        .order_by('-id')
        .first()
    )


def _sicar_spec_hint_from_filename(filename):
    """Retorna um dataset somente quando o nome do ZIP o discrimina sem empate.

    Isto não confirma um arquivo novo. O atalho só é usado quando o SHA-256 do
    arquivo é exatamente igual ao último arquivo já confirmado para UF+dataset.
    """
    ranked = []
    for spec in datasets_for_source('sicar'):
        hits = _filename_token_hits(filename, spec)
        if not hits:
            continue
        lengths = [len(norm(token)) for token in hits]
        ranked.append((max(lengths, default=0), len(hits), sum(lengths), spec))
    if not ranked:
        return None
    ranked.sort(key=lambda row: row[:3], reverse=True)
    best_rank = ranked[0][:3]
    leaders = [row[3] for row in ranked if row[:3] == best_rank]
    return leaders[0] if len(leaders) == 1 else None


def _try_sicar_sha_shortcut(item, archive):
    uf = normalize_uf(item.uf) or _detect_uf_hint(item.caminho_relativo) or _detect_uf_hint(item.nome_arquivo)
    if not uf or not item.hash_sha256:
        return None
    spec = _sicar_spec_hint_from_filename(archive.name)
    if not spec:
        return None
    previous = get_fingerprint(uf, spec.slug)
    if not previous or not previous.hash_arquivo or previous.hash_arquivo != item.hash_sha256:
        return None
    # GeoPackages SICAR atuais trazem DICIONARIO com metadados oficiais. Se a
    # versão histórica foi confirmada antes de o Manage registrar esse catálogo,
    # não usamos o atalho SHA: fazemos uma inspeção leve uma única vez para
    # capturar os metadados, sem reprocessar o PostGIS quando o conteúdo for igual.
    if Path(archive).suffix.lower() == '.gpkg':
        previous_result = (previous.ultima_importacao.resultado or {}) if previous.ultima_importacao_id else {}
        previous_metadata = previous_result.get('metadados_sicar') or {}
        if not previous_metadata.get('present'):
            return None
    if not sicar_partition_has_rows(spec, uf):
        # A fonte é conhecida, mas a partição operacional não está presente.
        # Não pulamos a carga: o arquivo volta ao fluxo completo para reparar a base.
        return None

    classification = {
        'status': 'CLASSIFICADO',
        'dataset_slug': spec.slug,
        'dataset_label': spec.label,
        'criterio': 'SHA256_ESTADUAL_JA_CONFIRMADO',
        'classificador_versao': BATCH_CLASSIFIER_VERSION,
        'sicar_uf': {'uf': uf, 'detectadas': [uf], 'confiavel': True},
        'fingerprint_conteudo': previous.hash_conteudo,
    }
    item.uf = uf
    item.dataset_slug = spec.slug
    item.dataset_label = spec.label
    item.fingerprint_conteudo = previous.hash_conteudo
    item.save(update_fields=['uf', 'dataset_slug', 'dataset_label', 'fingerprint_conteudo'])
    imp = _register_unchanged_import(item, spec, classification, uf, archive)
    _finalize_sicar_fingerprint(item, spec, imp, changed=False)
    return _finish_item(
        item,
        ItemLoteImportacao.Status.SEM_ALTERACAO,
        'Sem alteração — SHA-256 confirmado',
        'Arquivo idêntico à última versão confirmada para esta UF e camada. A extração GIS e o PostGIS foram ignorados.',
        importacao=imp,
    )


def _analyze_sicar_item(item, archive):
    lote = item.lote
    if _batch_interruption_requested(lote.pk):
        raise BatchInterruptionRequested('Interrupção solicitada antes da pré-análise SICAR.')
    _set_item_progress(item.pk, 8, 'Validando segurança e identificando o arquivo')
    # Arquivo sem alteração também passa pela política de segurança. O fato de o
    # conteúdo ser conhecido não autoriza pular validação/antimalware.
    _validate_input_security(archive, 'sicar')
    run_antivirus(archive)

    shortcut = _try_sicar_sha_shortcut(item, archive)
    if shortcut is not None:
        return shortcut

    spec, classification = classify_archive(
        archive, 'sicar', relative_path=item.caminho_relativo,
        archive_sha256=item.hash_sha256,
    )
    if spec is None:
        return _finish_item(
            item,
            ItemLoteImportacao.Status.REQUER_REVISAO,
            'Requer revisão',
            classification.get('motivo', 'Dataset não classificado.'),
        )

    item.dataset_slug = spec.slug
    item.dataset_label = spec.label
    item.hash_sha256 = item.hash_sha256 or hash_file(archive)
    item.fingerprint_conteudo = classification.get('fingerprint_conteudo', '')
    uf, uf_error = _resolve_sicar_uf(item, classification)
    if uf_error:
        item.save(update_fields=['dataset_slug', 'dataset_label', 'hash_sha256', 'fingerprint_conteudo'])
        return _finish_item(
            item, ItemLoteImportacao.Status.REQUER_REVISAO,
            'UF pendente de confirmação', uf_error,
        )

    item.uf = uf
    item.save(update_fields=['dataset_slug', 'dataset_label', 'hash_sha256', 'fingerprint_conteudo', 'uf'])
    mark_state_processing(uf, lote)
    _set_item_progress(item.pk, 45, f'{uf} identificado — comparando com a versão anterior')
    if _batch_interruption_requested(lote.pk):
        raise BatchInterruptionRequested('Interrupção solicitada durante a pré-análise SICAR.')

    if item.fingerprint_conteudo:
        same_content_other_state = SicarFingerprintCamada.objects.filter(
            dataset_slug=spec.slug,
            hash_conteudo=item.fingerprint_conteudo,
        ).exclude(uf=uf).first()
        if same_content_other_state:
            return _finish_item(
                item,
                ItemLoteImportacao.Status.REQUER_REVISAO,
                'Conteúdo associado a outra UF',
                f'O mesmo conteúdo já foi confirmado anteriormente para {same_content_other_state.uf}, '
                f'mas este item está associado a {uf}. A atualização foi bloqueada para revisão.',
            )

    previous_in_batch = _same_partition_item(lote, item, uf, spec.slug)
    if previous_in_batch and previous_in_batch.fingerprint_conteudo and item.fingerprint_conteudo:
        if previous_in_batch.fingerprint_conteudo == item.fingerprint_conteudo:
            return _finish_item(
                item,
                ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
                'Duplicado no próprio lote',
                f'O mesmo conteúdo de {spec.label} para {uf} já aparece anteriormente neste lote.',
            )
        return _finish_item(
            item,
            ItemLoteImportacao.Status.REQUER_REVISAO,
            'Duas versões da mesma UF/camada',
            f'O lote contém duas versões diferentes de {spec.label} para {uf}. '
            'A segunda versão foi bloqueada porque não há uma data de referência confiável '
            'para decidir automaticamente qual arquivo deve prevalecer.',
        )

    previous = get_fingerprint(uf, spec.slug)
    if (
        previous
        and item.fingerprint_conteudo
        and previous.hash_conteudo == item.fingerprint_conteudo
        and sicar_partition_has_rows(spec, uf)
    ):
        imp = _register_unchanged_import(item, spec, classification, uf, archive)
        _finalize_sicar_fingerprint(item, spec, imp, changed=False)
        return _finish_item(
            item,
            ItemLoteImportacao.Status.SEM_ALTERACAO,
            'Sem alteração — banco preservado',
            'Conteúdo igual à última versão confirmada. O PostGIS não será reprocessado.',
            importacao=imp,
        )

    return _finish_item(
        item,
        ItemLoteImportacao.Status.PRONTO_IMPORTAR,
        'Alteração detectada — aguardando confirmação',
        'Nova versão identificada. Nenhuma tabela operacional foi alterada nesta etapa.',
    )


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


def _import_classified_item(item, archive, source_slug):
    lote = item.lote
    _set_item_progress(item.pk, 8, 'Confirmando arquivo antes da importação')

    current_hash = hash_file(archive)
    if item.hash_sha256 and current_hash != item.hash_sha256:
        return _finish_item(
            item,
            ItemLoteImportacao.Status.REQUER_REVISAO,
            'Arquivo alterado após análise',
            'O arquivo mudou depois da pré-análise. Ele precisa ser analisado novamente antes de qualquer promoção.',
        )

    spec, classification = _classify_batch_input(
        archive, source_slug, relative_path=item.caminho_relativo, archive_sha256=current_hash,
    )
    if spec is None:
        return _finish_item(
            item,
            ItemLoteImportacao.Status.REQUER_REVISAO,
            'Requer revisão',
            classification.get('motivo', 'Dataset não classificado.'),
        )

    if item.dataset_slug and item.dataset_slug != spec.slug:
        return _finish_item(
            item,
            ItemLoteImportacao.Status.REQUER_REVISAO,
            'Dataset mudou após análise',
            f'A pré-análise identificou {item.dataset_slug}, mas a nova leitura identificou {spec.slug}. '
            'A promoção foi bloqueada.',
        )

    item.dataset_slug = spec.slug
    item.dataset_label = spec.label
    item.hash_sha256 = current_hash

    context = {
        'lote_id': lote.pk,
        'caminho_lote': item.caminho_relativo,
        'batch_classifier_version': BATCH_CLASSIFIER_VERSION,
        'batch_classification': classification,
    }
    if source_slug == 'prodes':
        filters = (lote.resultado or {}).get('filtros') or {}
        context['prodes_ano_inicial'] = normalize_prodes_start_year(
            filters.get('ano_inicial', DEFAULT_PRODES_START_YEAR)
        )

    if source_slug == 'sicar':
        uf, uf_error = _resolve_sicar_uf(item, classification)
        if uf_error:
            item.save(update_fields=['dataset_slug', 'dataset_label', 'hash_sha256'])
            return _finish_item(
                item, ItemLoteImportacao.Status.REQUER_REVISAO,
                'UF pendente de confirmação', uf_error,
            )
        if item.uf and normalize_uf(item.uf) != uf:
            return _finish_item(
                item,
                ItemLoteImportacao.Status.REQUER_REVISAO,
                'UF mudou após análise',
                f'A análise foi preparada para {item.uf}, mas o conteúdo agora aponta para {uf}. Nada foi promovido.',
            )
        fingerprint = classification.get('fingerprint_conteudo', '')
        if item.fingerprint_conteudo and fingerprint != item.fingerprint_conteudo:
            return _finish_item(
                item,
                ItemLoteImportacao.Status.REQUER_REVISAO,
                'Conteúdo mudou após análise',
                'O fingerprint do conteúdo não é mais o mesmo da pré-análise. O arquivo precisa ser analisado novamente.',
            )
        item.uf = uf
        item.fingerprint_conteudo = fingerprint
        item.save(update_fields=['dataset_slug', 'dataset_label', 'hash_sha256', 'uf', 'fingerprint_conteudo'])
        mark_state_processing(uf, lote)

        # Outra fila pode ter promovido exatamente a mesma versão enquanto este
        # lote aguardava confirmação do administrador.
        previous = get_fingerprint(uf, spec.slug)
        if (
            previous
            and fingerprint
            and previous.hash_conteudo == fingerprint
            and sicar_partition_has_rows(spec, uf)
        ):
            imp = _register_unchanged_import(item, spec, classification, uf, archive)
            _finalize_sicar_fingerprint(item, spec, imp, changed=False)
            return _finish_item(
                item,
                ItemLoteImportacao.Status.SEM_ALTERACAO,
                'Sem alteração — banco preservado',
                'A mesma versão já foi promovida antes desta fila ser executada.',
                importacao=imp,
            )

        context.update({
            'uf': uf,
            'fingerprint_conteudo': fingerprint,
            # Sempre valida integralmente a UF no staging. Histórico de SHA não
            # substitui a confirmação territorial antes de DELETE/INSERT estadual.
            'force_validate_uf': True,
        })
    else:
        item.save(update_fields=['dataset_slug', 'dataset_label', 'hash_sha256'])
        previous_same_dataset = list(ItemLoteImportacao.objects.filter(
            lote=lote,
            dataset_slug=spec.slug,
            id__lt=item.id,
            status__in=[
                ItemLoteImportacao.Status.CONCLUIDO,
                ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
                ItemLoteImportacao.Status.SEM_ALTERACAO,
            ],
        ))
        if previous_same_dataset:
            if spec.year_partitioned:
                current_year_hint = _year_hint_from_name(item.nome_arquivo)
                previous_year_hints = {
                    _year_hint_from_name(previous.nome_arquivo) for previous in previous_same_dataset
                }
                if current_year_hint is None or current_year_hint in previous_year_hints:
                    return _finish_item(
                        item,
                        ItemLoteImportacao.Status.REQUER_REVISAO,
                        'Requer revisão',
                        f'O lote contém mais de um arquivo para {spec.label} sem anos distintos confirmáveis. '
                        'O segundo arquivo não foi aplicado automaticamente para evitar ordem de versão ambígua.',
                    )
            else:
                return _finish_item(
                    item,
                    ItemLoteImportacao.Status.REQUER_REVISAO,
                    'Requer revisão',
                    f'O lote contém mais de um arquivo para {spec.label}. O segundo arquivo não foi aplicado '
                    'automaticamente para evitar ordem de versão ambígua.',
                )

    def progress(percent, stage):
        numeric = max(0, min(100, int(percent)))
        # Até o checkpoint de publicação (82%) é seguro abortar sem alterar a
        # base ativa. No SICOR as etapas pesadas agora são preparadas no staging
        # antes deste ponto. Depois dele deixamos a transação atômica terminar.
        if numeric <= 82 and _batch_interruption_requested(lote.pk):
            raise BatchInterruptionRequested('Interrupção solicitada pelo administrador antes da publicação atômica.')
        scaled = 12 + round(numeric * 0.86)
        _set_item_progress(item.pk, min(98, scaled), stage)

    if _batch_interruption_requested(lote.pk):
        return _finish_item(
            item, ItemLoteImportacao.Status.INTERROMPIDO, 'Interrompido',
            'O lote foi interrompido antes do pipeline iniciar.', progress=item.progresso,
        )

    _set_item_progress(item.pk, 15, 'Preparando pipeline GIS')
    with archive.open('rb') as raw:
        django_file = File(raw, name=archive.name)
        imp = process_import(
            django_file,
            spec.slug,
            lote.administrador,
            context=context,
            progress_callback=progress,
        )

    if imp.status == Importacao.Status.CONCLUIDO:
        success_message = ''
        success_stage = 'Atualizado com sucesso'
        if source_slug == 'sicar':
            _finalize_sicar_fingerprint(item, spec, imp, changed=True)
            uf_report = (imp.resultado or {}).get('ufs_sicar_detectadas') or {}
            extra_ufs = list(uf_report.get('ufs_adicionais_aceitas') or [])
            if extra_ufs:
                success_message = (
                    f'UF administrativa {item.uf} atualizada. CARs de divisa também consolidados: '
                    + ', '.join(extra_ufs) + '.'
                )
        elif source_slug == 'prodes':
            filter_report = (imp.resultado or {}).get('filtro_prodes') or {}
            invalid_years = int(filter_report.get('registros_ano_invalido') or 0)
            if invalid_years:
                success_stage = 'Atualizado com pendências'
                success_message = (
                    f'{invalid_years} registro(s) sem ano válido foram excluídos somente da carga '
                    'e contabilizados no relatório. Os registros válidos foram importados.'
                )

        geometry_report = (imp.resultado or {}).get('reparo_geometrias') or {}
        geometry_pending = int(geometry_report.get('nao_reparaveis') or 0)
        if geometry_pending:
            success_stage = 'Atualizado com pendências'
            geometry_message = (
                f'{geometry_pending} geometria(s) não puderam ser reparadas com segurança; '
                'foram preservadas na RAW e excluídas somente da tabela operacional. '
                'As geometrias válidas e reparáveis foram importadas normalmente.'
            )
            success_message = f'{success_message} {geometry_message}'.strip()
        return _finish_item(
            item, ItemLoteImportacao.Status.CONCLUIDO,
            success_stage, success_message, importacao=imp,
        )
    if imp.status == Importacao.Status.INTERROMPIDO:
        return _finish_item(
            item, ItemLoteImportacao.Status.INTERROMPIDO, 'Interrompido com segurança',
            imp.motivo_rejeicao or 'Processamento interrompido antes da publicação atômica.',
            importacao=imp, progress=item.progresso,
        )
    if imp.status in {Importacao.Status.IGNORADO_DUPLICADO, Importacao.Status.SEM_ALTERACAO}:
        if source_slug == 'sicar':
            _finalize_sicar_fingerprint(item, spec, imp, changed=False)
            status = ItemLoteImportacao.Status.SEM_ALTERACAO
        else:
            status = (
                ItemLoteImportacao.Status.SEM_ALTERACAO
                if imp.status == Importacao.Status.SEM_ALTERACAO
                else ItemLoteImportacao.Status.IGNORADO_DUPLICADO
            )
        return _finish_item(
            item, status, 'Sem alteração — banco preservado',
            (
                'O conteúdo após as regras de tratamento é igual à versão conhecida. Nenhuma escrita foi feita no banco.'
                if imp.status == Importacao.Status.SEM_ALTERACAO
                else 'Arquivo já corresponde à versão conhecida. Nenhum reprocessamento foi necessário.'
            ),
            importacao=imp,
        )
    return _finish_item(
        item, ItemLoteImportacao.Status.FALHOU, 'Falhou',
        imp.motivo_rejeicao or imp.get_status_display(), importacao=imp,
    )


def process_batch_item(item):
    item = ItemLoteImportacao.objects.select_related('lote', 'lote__administrador').get(pk=item.pk)
    lote = item.lote
    source_slug = _source_slug_from_value(lote.fonte)
    phase = str((lote.resultado or {}).get('fase') or ('ANALISE' if source_slug == 'sicar' else 'IMPORTACAO')).upper()
    try:
        if _batch_interruption_requested(lote.pk):
            _finish_item(
                item, ItemLoteImportacao.Status.INTERROMPIDO, 'Interrompido',
                'O lote foi interrompido antes deste item iniciar.', progress=item.progresso,
            )
        else:
            archive = _item_archive_path(item)
            if source_slug == 'sicar' and phase == 'ANALISE':
                _analyze_sicar_item(item, archive)
            else:
                _import_classified_item(item, archive, source_slug)
    except BatchInterruptionRequested as exc:
        _finish_item(item, ItemLoteImportacao.Status.INTERROMPIDO, 'Interrompido com segurança', str(exc), progress=item.progresso)
    except Exception as exc:
        logger.exception('Falha no item %s do lote %s', item.pk, lote.pk)
        _finish_item(item, ItemLoteImportacao.Status.FALHOU, 'Falhou', str(exc))
    update_batch_status(lote.pk)
    finished_item = ItemLoteImportacao.objects.select_related('lote').get(pk=item.pk)
    _cleanup_finished_item_file(finished_item)
    return finished_item



def calculate_batch_progress(lote, itens=None):
    """Progresso da fase atual, incluindo arquivos ainda não enviados no modo sequencial."""
    if itens is None:
        itens = list(lote.itens.only('id', 'progresso'))
    else:
        itens = list(itens)

    result = lote.resultado or {}
    if result.get('modo') == 'UPLOAD_SEQUENCIAL':
        expected = max(1, int(result.get('arquivos_esperados') or 1))
        # Arquivos ainda não enviados equivalem a 0%. Cada item já recebido
        # contribui com seu progresso real, portanto 1 de 6 concluído = 17%.
        return round(sum(int(item.progresso or 0) for item in itens) / expected)

    if not itens:
        return 0
    phase = str(result.get('fase') or '').upper()
    confirmed_ids = {int(value) for value in (result.get('itens_confirmados') or []) if str(value).isdigit()}
    if phase == 'IMPORTACAO' and confirmed_ids:
        phase_items = [item for item in itens if item.id in confirmed_ids]
        if phase_items:
            itens = phase_items
    return round(sum(int(item.progresso or 0) for item in itens) / len(itens))


def confirm_batch_changes(lote_id, usuario):
    """Confirma a segunda fase do lote SICAR sem reclassificar dados silenciosamente."""
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        if _source_slug_from_value(lote.fonte) != 'sicar':
            raise ValueError('A confirmação em duas etapas é exclusiva do fluxo SICAR.')
        if lote.status != LoteImportacao.Status.AGUARDANDO_CONFIRMACAO:
            raise ValueError('Este lote não está aguardando confirmação de importação.')
        ready = lote.itens.filter(status=ItemLoteImportacao.Status.PRONTO_IMPORTAR)
        confirmed_ids = list(ready.values_list('id', flat=True))
        quantidade = len(confirmed_ids)
        if not quantidade:
            raise ValueError('Nenhuma alteração está pronta para importação.')
        ready.update(
            status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
            progresso=0,
            etapa='Aguardando importação na fila',
            motivo='',
            iniciado_em=None,
            finalizado_em=None,
            importacao=None,
        )
        result = dict(lote.resultado or {})
        result['fase'] = 'IMPORTACAO'
        result['alteracoes_confirmadas'] = quantidade
        result['itens_confirmados'] = confirmed_ids
        result['progresso_percentual'] = 0
        result['confirmado_em'] = timezone.now().isoformat()
        lote.resultado = result
        lote.status = LoteImportacao.Status.PROCESSANDO
        lote.data_finalizacao = None
        lote.save(update_fields=['resultado', 'status', 'data_finalizacao'])

    registrar_auditoria(
        usuario,
        'SICAR_LOTE_IMPORTACAO_CONFIRMADA',
        'LoteImportacao',
        lote.pk,
        {'alteracoes_confirmadas': quantidade},
    )
    _update_sicar_states_for_batch(lote)
    return lote


def _sicar_completeness(lote):
    source_slug = _source_slug_from_value(lote.fonte)
    if source_slug != 'sicar':
        return {}
    expected = {spec.slug: spec.label for spec in datasets_for_source('sicar')}
    result = {}
    for slug, label in expected.items():
        qs = lote.itens.filter(dataset_slug=slug)
        result[slug] = {
            'label': label,
            'arquivos': qs.count(),
            'alteracoes_detectadas': qs.filter(status=ItemLoteImportacao.Status.PRONTO_IMPORTAR).count(),
            'concluidos': qs.filter(status__in=[
                ItemLoteImportacao.Status.CONCLUIDO,
                ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
                ItemLoteImportacao.Status.SEM_ALTERACAO,
            ]).count(),
            'falhas': qs.filter(status__in=[
                ItemLoteImportacao.Status.FALHOU,
                ItemLoteImportacao.Status.REQUER_REVISAO,
            ]).count(),
        }
    return result


def _update_sicar_states_for_batch(lote):
    if _source_slug_from_value(lote.fonte) != 'sicar':
        return
    ufs = list(lote.itens.exclude(uf='').values_list('uf', flat=True).distinct())
    for uf in ufs:
        qs = lote.itens.filter(uf=uf)
        statuses = list(qs.values_list('status', flat=True))
        state, _ = SicarEstado.objects.get_or_create(uf=uf)
        state.ultimo_lote = lote
        geometry_pending = 0
        for concluded_item in qs.filter(
            status=ItemLoteImportacao.Status.CONCLUIDO,
            importacao__isnull=False,
        ).select_related('importacao'):
            geometry_report = (concluded_item.importacao.resultado or {}).get('reparo_geometrias') or {}
            geometry_pending += int(geometry_report.get('nao_reparaveis') or 0)
        state.detalhes = {
            'lote_id': lote.pk,
            'fase': (lote.resultado or {}).get('fase', ''),
            'arquivos': qs.count(),
            'datasets': list(qs.exclude(dataset_slug='').values_list('dataset_slug', flat=True).distinct()),
            'contagens': {status: statuses.count(status) for status, _label in ItemLoteImportacao.Status.choices},
            'pendencias_geometria': geometry_pending,
        }

        own_pending = any(status in {
            ItemLoteImportacao.Status.AGUARDANDO_FILA,
            ItemLoteImportacao.Status.PENDENTE,
            ItemLoteImportacao.Status.PROCESSANDO,
        } for status in statuses)
        if own_pending:
            state.status = SicarEstado.Status.PROCESSANDO
            state.save(update_fields=['status', 'ultimo_lote', 'detalhes', 'atualizado_em'])
            continue

        # Problemas têm prioridade visual sobre alterações aguardando confirmação.
        if ItemLoteImportacao.Status.FALHOU in statuses:
            state.status = SicarEstado.Status.FALHOU
        elif ItemLoteImportacao.Status.REQUER_REVISAO in statuses or geometry_pending:
            state.status = SicarEstado.Status.ATENCAO
        elif ItemLoteImportacao.Status.PRONTO_IMPORTAR in statuses:
            state.status = SicarEstado.Status.EM_FILA
        elif ItemLoteImportacao.Status.CONCLUIDO in statuses:
            state.status = SicarEstado.Status.ATUALIZADO
        elif statuses and all(status in {
            ItemLoteImportacao.Status.SEM_ALTERACAO,
            ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
        } for status in statuses):
            state.status = SicarEstado.Status.SEM_ALTERACAO
        else:
            state.status = SicarEstado.Status.NUNCA_IMPORTADO if not state.ultima_atualizacao else state.status

        successful = list(qs.filter(status__in=[
            ItemLoteImportacao.Status.CONCLUIDO,
            ItemLoteImportacao.Status.SEM_ALTERACAO,
        ]).values_list('finalizado_em', flat=True))
        successful = [value for value in successful if value]
        if successful:
            newest_verification = max(successful)
            if not state.ultima_verificacao or newest_verification > state.ultima_verificacao:
                state.ultima_verificacao = newest_verification

        updates = [
            item.importacao.data_finalizacao
            for item in qs.filter(status=ItemLoteImportacao.Status.CONCLUIDO).select_related('importacao')
            if item.importacao and item.importacao.data_finalizacao
        ]
        if updates:
            newest_update = max(updates)
            if not state.ultima_atualizacao or newest_update > state.ultima_atualizacao:
                state.ultima_atualizacao = newest_update

        state.save(update_fields=[
            'status', 'ultima_verificacao', 'ultima_atualizacao',
            'ultimo_lote', 'detalhes', 'atualizado_em',
        ])


def retry_failed_batch_items(lote_id, usuario):
    """Reenfileira somente falhas técnicas sem transformar revisão em importação.

    Itens em REQUER_REVISAO continuam exigindo uma decisão humana. No SICAR,
    toda falha volta para a fase de pré-análise; isso evita que um arquivo que
    falhou antes da confirmação administrativa seja promovido diretamente em
    uma tentativa posterior.
    """
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        failed = lote.itens.filter(status=ItemLoteImportacao.Status.FALHOU)
        quantidade = failed.count()
        if not quantidade:
            raise ValueError('Não há itens com falha técnica para reprocessar neste lote.')

        failed.update(
            status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
            progresso=0,
            etapa='Aguardando nova análise na fila',
            motivo='',
            iniciado_em=None,
            finalizado_em=None,
            importacao=None,
            fingerprint_conteudo='',
        )

        result = dict(lote.resultado or {})
        if _source_slug_from_value(lote.fonte) == 'sicar':
            result['fase'] = 'ANALISE'
            result.pop('itens_confirmados', None)
            result.pop('alteracoes_confirmadas', None)
            lote.status = LoteImportacao.Status.ANALISANDO
        else:
            result['fase'] = 'IMPORTACAO'
            lote.status = LoteImportacao.Status.PROCESSANDO
        result['reprocessamento_falhas_em'] = timezone.now().isoformat()
        result['reprocessamento_falhas_quantidade'] = quantidade
        result['progresso_percentual'] = 0
        lote.resultado = result
        lote.data_finalizacao = None
        lote.motivo_falha = ''
        lote.save(update_fields=['resultado', 'status', 'data_finalizacao', 'motivo_falha'])

    registrar_auditoria(
        usuario,
        'LOTE_FALHAS_REENFILEIRADAS',
        'LoteImportacao',
        lote.pk,
        {'quantidade': quantidade, 'fonte': str(lote.fonte)},
    )
    lote = update_batch_status(lote.pk)
    return lote


def retry_review_batch_items(lote_id, usuario):
    """Reexecuta classificação de itens em revisão de fontes não SICAR.

    Útil quando a política de classificação foi atualizada (por exemplo PRODES
    v4). Se a ambiguidade continuar, o item voltará a REQUER_REVISAO; nenhuma
    promoção é forçada. SICAR mantém o fluxo específico de confirmação de UF.
    """
    with transaction.atomic():
        lote = LoteImportacao.objects.select_for_update().get(pk=lote_id)
        source_slug = _source_slug_from_value(lote.fonte)
        if source_slug == 'sicar':
            raise ValueError('No SICAR, itens em revisão devem ser corrigidos pelo fluxo de UF/camada.')
        review = lote.itens.filter(status=ItemLoteImportacao.Status.REQUER_REVISAO)
        quantidade = review.count()
        if not quantidade:
            raise ValueError('Não há itens em revisão para reanalisar neste lote.')
        review.update(
            status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
            progresso=0,
            etapa='Aguardando nova classificação na fila',
            motivo='',
            iniciado_em=None,
            finalizado_em=None,
            importacao=None,
        )
        result = dict(lote.resultado or {})
        result['fase'] = 'IMPORTACAO'
        result['reanalise_revisoes_em'] = timezone.now().isoformat()
        result['reanalise_revisoes_quantidade'] = quantidade
        result['classificador_versao'] = BATCH_CLASSIFIER_VERSION
        result['progresso_percentual'] = 0
        lote.resultado = result
        lote.status = LoteImportacao.Status.PROCESSANDO
        lote.data_finalizacao = None
        lote.save(update_fields=['resultado', 'status', 'data_finalizacao'])

    registrar_auditoria(
        usuario,
        'LOTE_REVISOES_REANALISADAS',
        'LoteImportacao',
        lote.pk,
        {'quantidade': quantidade, 'fonte': str(lote.fonte), 'classificador_versao': BATCH_CLASSIFIER_VERSION},
    )
    return update_batch_status(lote.pk)


def update_batch_status(lote_id):
    lote = LoteImportacao.objects.get(pk=lote_id)
    counts = {
        status: lote.itens.filter(status=status).count()
        for status, _ in ItemLoteImportacao.Status.choices
    }
    items = list(lote.itens.only('id', 'progresso'))
    overall_progress = calculate_batch_progress(lote, items)
    result = dict(lote.resultado or {})
    phase = str(result.get('fase') or ('ANALISE' if _source_slug_from_value(lote.fonte) == 'sicar' else 'IMPORTACAO')).upper()
    result['fase'] = phase
    result['contagens'] = counts
    result['progresso_percentual'] = overall_progress
    result['sicar_datasets'] = _sicar_completeness(lote)
    lote.resultado = result

    if result.get('interrupcao_solicitada'):
        active = counts.get(ItemLoteImportacao.Status.PROCESSANDO, 0)
        lote.status = LoteImportacao.Status.INTERROMPENDO if active else LoteImportacao.Status.INTERROMPIDO
        lote.data_finalizacao = None if active else timezone.now()
        if not active:
            result['interrompido_em'] = lote.data_finalizacao.isoformat()
            result['progresso_percentual'] = overall_progress
        lote.resultado = result
        lote.save(update_fields=['status', 'data_finalizacao', 'resultado'])
        if not active:
            _cleanup_finished_batch_files(lote)
        return lote

    pending = (
        counts.get(ItemLoteImportacao.Status.AGUARDANDO_FILA, 0)
        + counts.get(ItemLoteImportacao.Status.PENDENTE, 0)
        + counts.get(ItemLoteImportacao.Status.PROCESSANDO, 0)
    )
    if pending:
        lote.status = (
            LoteImportacao.Status.ANALISANDO
            if _source_slug_from_value(lote.fonte) == 'sicar' and phase == 'ANALISE'
            else LoteImportacao.Status.PROCESSANDO
        )
        lote.data_finalizacao = None
        lote.save(update_fields=['status', 'data_finalizacao', 'resultado'])
        _update_sicar_states_for_batch(lote)
        return lote

    # No envio sequencial, terminar um item não encerra o lote: o navegador
    # ainda precisa enviar o próximo arquivo selecionado. Só após o endpoint
    # de finalização o lote pode ir para confirmação SICAR ou estado final.
    if result.get('modo') == 'UPLOAD_SEQUENCIAL' and not result.get('sequencial_finalizado'):
        result['sequencial_aguardando_upload'] = True
        lote.resultado = result
        lote.status = (
            LoteImportacao.Status.ANALISANDO
            if _source_slug_from_value(lote.fonte) == 'sicar' and phase == 'ANALISE'
            else LoteImportacao.Status.PROCESSANDO
        )
        lote.data_finalizacao = None
        lote.save(update_fields=['status', 'data_finalizacao', 'resultado'])
        _update_sicar_states_for_batch(lote)
        return lote

    if _source_slug_from_value(lote.fonte) == 'sicar' and phase == 'ANALISE':
        ready = counts.get(ItemLoteImportacao.Status.PRONTO_IMPORTAR, 0)
        if ready:
            result['analise_concluida_em'] = timezone.now().isoformat()
            result['alteracoes_detectadas'] = ready
            result['progresso_percentual'] = 100
            lote.resultado = result
            lote.status = LoteImportacao.Status.AGUARDANDO_CONFIRMACAO
            lote.data_finalizacao = None
            lote.save(update_fields=['status', 'data_finalizacao', 'resultado'])
            _update_sicar_states_for_batch(lote)
            return lote

    report_warnings = 0
    geometry_warnings = 0
    temporal_warnings = 0
    date_warnings = 0
    concluded_items = lote.itens.filter(
        status=ItemLoteImportacao.Status.CONCLUIDO,
        importacao__isnull=False,
    ).select_related('importacao')
    for concluded_item in concluded_items:
        import_result = concluded_item.importacao.resultado or {}
        geometry_report = import_result.get('reparo_geometrias') or {}
        geometry_warnings += int(geometry_report.get('nao_reparaveis') or 0)
        if _source_slug_from_value(lote.fonte) == 'prodes':
            filter_report = import_result.get('filtro_prodes') or {}
            temporal_warnings += int(filter_report.get('registros_ano_invalido') or 0)
        date_profiles = (((import_result.get('promocao') or {}).get('normalizacao') or {}).get('normalizacao_datas') or {})
        for profile in date_profiles.values():
            date_warnings += int(profile.get('nao_reconhecidos') or 0)
            if not profile.get('preferencia_ambiguos'):
                date_warnings += int(profile.get('ambiguos') or 0)
    report_warnings = geometry_warnings + temporal_warnings + date_warnings
    result['pendencias_geometria'] = geometry_warnings
    result['pendencias_temporais'] = temporal_warnings
    result['pendencias_datas'] = date_warnings
    result['pendencias_relatorio'] = report_warnings

    has_problem = bool(
        counts.get(ItemLoteImportacao.Status.FALHOU)
        or counts.get(ItemLoteImportacao.Status.REQUER_REVISAO)
        or counts.get(ItemLoteImportacao.Status.PRONTO_IMPORTAR)
        or report_warnings
    )
    lote.status = (
        LoteImportacao.Status.CONCLUIDO_COM_PENDENCIAS
        if has_problem else LoteImportacao.Status.CONCLUIDO
    )
    lote.data_finalizacao = timezone.now()
    result['progresso_percentual'] = 100
    lote.resultado = result
    lote.save(update_fields=['status', 'data_finalizacao', 'resultado'])
    _update_sicar_states_for_batch(lote)
    _cleanup_finished_batch_files(lote)
    return lote


def _cleanup_finished_batch_files(lote):
    # Política conservadora: só limpamos quando TODOS os itens estão em estados
    # finais que não podem ser reprocessados/revisados. Uma situação nova ou um
    # status futuro fica preservado por padrão em vez de apagar o lote.
    cleanup_safe_statuses = [
        ItemLoteImportacao.Status.CONCLUIDO,
        ItemLoteImportacao.Status.IGNORADO_DUPLICADO,
        ItemLoteImportacao.Status.SEM_ALTERACAO,
        ItemLoteImportacao.Status.INTERROMPIDO,
    ]
    if lote.itens.exclude(status__in=cleanup_safe_statuses).exists():
        return
    if (lote.resultado or {}).get('modo') == 'PASTA_MONITORADA':
        path = Path(lote.extracted_path)
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        if path.exists():
            for marker in path.glob(f'PROCESSANDO_CONFRONTA_{lote.pk}.txt'):
                marker.unlink(missing_ok=True)
            result_marker = path / f'RESULTADO_CONFRONTA_{lote.pk}.txt'
            result_marker.write_text(
                f'Lote #{lote.pk}\nStatus: {lote.get_status_display()}\nFinalizado em: {lote.data_finalizacao}\n',
                encoding='utf-8',
            )
        return
    for path in _batch_root_candidates(lote):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    if lote.quarantine_path:
        path = Path(lote.quarantine_path)
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        if path.exists() and path.is_file():
            path.unlink(missing_ok=True)
    for recovery_root in _batch_recovery_roots(lote.pk):
        if recovery_root.exists():
            shutil.rmtree(recovery_root, ignore_errors=True)


def recover_stale_items(minutes=120):
    cutoff = timezone.now() - timedelta(minutes=minutes)
    return ItemLoteImportacao.objects.filter(
        status=ItemLoteImportacao.Status.PROCESSANDO,
        iniciado_em__lt=cutoff,
        lote__status__in=[LoteImportacao.Status.ANALISANDO, LoteImportacao.Status.PROCESSANDO],
    ).update(
        status=ItemLoteImportacao.Status.AGUARDANDO_FILA,
        motivo='Item recuperado automaticamente após interrupção do worker.',
        iniciado_em=None,
        progresso=0,
        etapa='Recuperado — aguardando reprocessamento',
    )
