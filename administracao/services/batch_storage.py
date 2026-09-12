"""Infraestrutura de storage e recovery dos lotes de importa??o.

Este m?dulo foi extra?do de batch.py sem altera??o das regras de neg?cio.
batch.py continua funcionando como fachada de compatibilidade.
"""

import logging
import os
import shutil
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

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
