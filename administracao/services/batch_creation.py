import hashlib
import logging
import shutil
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from administracao.constants import FONTE_SLUGS
from administracao.models import ItemLoteImportacao, LoteImportacao

from .auditoria import registrar_auditoria
from .batch_classification import _detect_uf_hint
from .batch_storage import (
    _batch_recovery_roots,
    _create_recovery_link,
)
from .batch_upload import (
    BatchUploadLimitError,
    _save_batch_upload,
    _upload_limits,
    _validate_input_extension,
    ensure_batch_disk_space,
    save_uploaded_file,
    validate_upload_limits,
)
from .extraction import extract_zip_safely
from .partitioning import normalize_uf
from .prodes_filter import normalize_prodes_start_year
from .sicar_tracking import hash_file
from .zip_security import run_antivirus, validate_zip


logger = logging.getLogger(__name__)


def create_batch(uploaded_file, source_slug, usuario, default_uf='', prodes_start_year=None):
    fonte = FONTE_SLUGS.get(source_slug)
    if not fonte:
        raise ValueError('Fonte não cadastrada para importação em lote.')

    validate_upload_limits([uploaded_file])

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
        batch_zip, digest, size = _save_batch_upload(uploaded_file, lote.pk)
        lote.hash_sha256 = digest
        lote.tamanho_bytes = size
        lote.quarantine_path = str(batch_zip.relative_to(settings.BASE_DIR))
        lote.save(update_fields=['hash_sha256','tamanho_bytes','quarantine_path'])

        security = validate_zip(batch_zip)
        ensure_batch_disk_space(
            extra_extraction_bytes=security['conteudo_descompactado_bytes'],
            recovery_bytes=security['conteudo_descompactado_bytes'],
        )
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
        lote.motivo_falha = (
            str(exc) if isinstance(exc, BatchUploadLimitError)
            else 'Espaço de armazenamento insuficiente para processar este lote com segurança.'
            if isinstance(exc, OSError) else str(exc)
        )
        lote.data_finalizacao = timezone.now()
        lote.save(update_fields=['status','motivo_falha','data_finalizacao','extracted_path'])
        registrar_auditoria(
            usuario, 'LOTE_IMPORTACAO_FALHOU', 'LoteImportacao', lote.pk,
            {'motivo': lote.motivo_falha},
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
    declared_total = validate_upload_limits(files)

    lote = LoteImportacao.objects.create(
        fonte=fonte, nome_arquivo_original=f'{len(files)} arquivo(s) selecionado(s)',
        hash_sha256='0' * 64, tamanho_bytes=0, administrador=usuario,
        status=LoteImportacao.Status.PREPARANDO,
    )
    extracted = Path(settings.BATCH_DIR) / f'lote_{lote.pk}'
    manifest = hashlib.sha256()
    total = 0
    total_uncompressed = 0
    try:
        extracted.mkdir(parents=True, exist_ok=True)
        for index, uploaded in enumerate(files, 1):
            _validate_input_extension(uploaded.name, source_slug)
            safe_name = Path(uploaded.name).name
            item_dir = extracted / f'item_{index:04d}'
            item_dir.mkdir(parents=True, exist_ok=True)
            target = item_dir / safe_name
            file_hash = hashlib.sha256()
            digest, size = save_uploaded_file(
                uploaded, target, existing_batch_bytes=total, digest=file_hash,
            )
            total += size
            if not target.is_file():
                raise IOError(f'O arquivo {safe_name} não permaneceu disponível na área compartilhada do lote.')
            expected_size = int(getattr(uploaded, 'size', 0) or 0)
            if expected_size and target.stat().st_size != expected_size:
                raise IOError(
                    f'O arquivo {safe_name} foi gravado com tamanho divergente '
                    f'({target.stat().st_size} de {expected_size} bytes). O lote foi bloqueado.'
                )
            if target.suffix.lower() == '.zip':
                security = validate_zip(target)
                total_uncompressed += security['conteudo_descompactado_bytes']
                ensure_batch_disk_space(
                    max(0, declared_total - total),
                    extra_extraction_bytes=total_uncompressed,
                    recovery_bytes=max(0, declared_total - total) + size,
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
        lote.motivo_falha = (
            str(exc) if isinstance(exc, BatchUploadLimitError)
            else 'Espaço de armazenamento insuficiente para processar este lote com segurança.'
            if isinstance(exc, OSError) else str(exc)
        )
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
    _max_file, _max_total, max_files = _upload_limits()
    if expected > min(max_files, 500):
        raise BatchUploadLimitError('O lote contém mais arquivos que o permitido.')

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
