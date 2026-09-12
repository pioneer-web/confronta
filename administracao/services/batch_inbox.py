import hashlib
import logging
import os
from pathlib import Path

from django.conf import settings
from django.db import transaction

from administracao.constants import BATCH_FONTE_SLUGS, FONTE_SLUGS
from administracao.models import ItemLoteImportacao, LoteImportacao

from .auditoria import registrar_auditoria
from .batch_classification import _detect_uf_hint
from .batch_upload import _allowed_input_extensions
from .prodes_filter import DEFAULT_PRODES_START_YEAR
from .sicar_tracking import hash_file


logger = logging.getLogger(__name__)


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
