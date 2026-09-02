import os
import shlex
import stat
import subprocess
import zipfile
import sqlite3
from pathlib import Path
from pathlib import PurePosixPath
from django.conf import settings
from .exceptions import SecurityValidationError

# Política aprovada para uploads internos do CONFRONTA:
# bloquear somente extensões executáveis/scripts explicitamente listadas.
# Demais extensões auxiliares de pacotes oficiais (ex.: .qmd, .fix) são aceitas.
BLOCKED_EXECUTABLE_EXTENSIONS = {
    # Windows
    '.exe', '.dll', '.msi', '.bat', '.cmd', '.com', '.pif', '.scr',
    # Linux / Unix
    '.sh', '.bin', '.run',
}


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def validate_zip(path):
    """Valida o contêiner ZIP sem restringir extensões de dados oficiais.

    A política de extensão bloqueia exclusivamente executáveis/scripts definidos
    em BLOCKED_EXECUTABLE_EXTENSIONS. Proteções estruturais do próprio ZIP são
    preservadas para evitar extração fora da quarentena, links simbólicos,
    arquivos criptografados e expansão acidental excessiva.
    """
    path = str(path)
    if not zipfile.is_zipfile(path):
        raise SecurityValidationError('O arquivo enviado não é um ZIP válido.')

    file_size = os.path.getsize(path)
    if settings.MAX_UPLOAD_SIZE_BYTES and file_size > settings.MAX_UPLOAD_SIZE_BYTES:
        raise SecurityValidationError('O arquivo ZIP excede o limite configurado para upload.')

    total_uncompressed = 0
    total_compressed = 0
    seen = set()

    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if not infos:
            raise SecurityValidationError('O arquivo ZIP está vazio.')
        if len(infos) > settings.MAX_ZIP_ENTRIES:
            raise SecurityValidationError('O ZIP possui quantidade excessiva de entradas.')

        for info in infos:
            if info.is_dir():
                continue

            raw_name = info.filename.replace('\\', '/')
            pure = PurePosixPath(raw_name)

            # Proteções estruturais da extração. Não dependem do tipo de dado.
            if pure.is_absolute() or '..' in pure.parts:
                raise SecurityValidationError(f'Caminho inseguro detectado no ZIP: {raw_name}')
            if pure.parts and ':' in pure.parts[0]:
                raise SecurityValidationError(f'Caminho absoluto do Windows detectado: {raw_name}')

            normalized = str(pure).lower()
            if normalized in seen:
                raise SecurityValidationError(f'Entrada duplicada detectada no ZIP: {raw_name}')
            seen.add(normalized)

            if _is_symlink(info):
                raise SecurityValidationError(f'Link simbólico não permitido: {raw_name}')
            if info.flag_bits & 0x1:
                raise SecurityValidationError(f'Arquivo criptografado não permitido: {raw_name}')

            ext = pure.suffix.lower()
            if ext in BLOCKED_EXECUTABLE_EXTENSIONS:
                raise SecurityValidationError(f'Arquivo executável não permitido detectado: {raw_name}')

            # Defesa adicional contra executável renomeado. Não bloqueia formatos
            # auxiliares desconhecidos; somente assinaturas típicas de executáveis/scripts.
            with zf.open(info, 'r') as entry:
                header = entry.read(8)
            if header.startswith(b'MZ') or header.startswith(b'\x7fELF') or header.startswith(b'#!'):
                raise SecurityValidationError(
                    f'Conteúdo executável/script detectado em arquivo não permitido: {raw_name}'
                )

            total_uncompressed += info.file_size
            total_compressed += max(info.compress_size, 1)

    if settings.MAX_ZIP_UNCOMPRESSED_BYTES and total_uncompressed > settings.MAX_ZIP_UNCOMPRESSED_BYTES:
        raise SecurityValidationError('O conteúdo descompactado excede o limite configurado.')

    ratio = total_uncompressed / max(total_compressed, 1)
    if settings.MAX_ZIP_EXPANSION_RATIO and ratio > settings.MAX_ZIP_EXPANSION_RATIO:
        raise SecurityValidationError('Taxa de expansão excessiva detectada no ZIP.')

    return {
        'arquivo_comprimido_bytes': file_size,
        'conteudo_descompactado_bytes': total_uncompressed,
        'entradas': len(seen),
        'taxa_expansao': round(ratio, 2),
        'politica_extensoes': 'bloqueia_apenas_executaveis_aprovados',
    }



def validate_gpkg(path):
    """Valida um GeoPackage direto antes de qualquer leitura GIS.

    A validação é deliberadamente conservadora: confirma extensão, limite de
    tamanho, assinatura SQLite e tabelas mínimas exigidas pelo GeoPackage. A
    leitura é aberta em modo somente-leitura. A validação estrutural das camadas,
    CRS, campos e geometrias continua sendo responsabilidade do pipeline GIS.
    """
    path = Path(path)
    if path.suffix.lower() != '.gpkg':
        raise SecurityValidationError('O arquivo enviado não possui extensão .gpkg.')
    if not path.is_file():
        raise SecurityValidationError('O arquivo GeoPackage não está disponível para validação.')

    file_size = path.stat().st_size
    if settings.MAX_UPLOAD_SIZE_BYTES and file_size > settings.MAX_UPLOAD_SIZE_BYTES:
        raise SecurityValidationError('O arquivo GPKG excede o limite configurado para upload.')
    if file_size < 100:
        raise SecurityValidationError('O arquivo GPKG está vazio ou incompleto.')

    with path.open('rb') as fh:
        header = fh.read(16)
    if header != b'SQLite format 3\x00':
        raise SecurityValidationError('O arquivo .gpkg não possui uma assinatura SQLite/GeoPackage válida.')

    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('gpkg_contents','gpkg_spatial_ref_sys')"
                )
            }
            required = {'gpkg_contents', 'gpkg_spatial_ref_sys'}
            missing = sorted(required - tables)
            if missing:
                raise SecurityValidationError(
                    'O arquivo SQLite não confirma a estrutura mínima de um GeoPackage: faltam ' + ', '.join(missing) + '.'
                )
            content_count = int(conn.execute('SELECT COUNT(*) FROM gpkg_contents').fetchone()[0])
        finally:
            conn.close()
    except SecurityValidationError:
        raise
    except sqlite3.Error as exc:
        raise SecurityValidationError(f'Não foi possível validar a estrutura do GeoPackage: {exc}') from exc

    if content_count < 1:
        raise SecurityValidationError('O GeoPackage não possui camadas registradas em gpkg_contents.')

    return {
        'formato': 'GPKG',
        'arquivo_bytes': file_size,
        'camadas_registradas': content_count,
        'container': 'sqlite_geopackage',
        'politica_extensoes': 'arquivo_direto_validado',
    }

def _decode_tool_output(data):
    if data is None:
        return ''
    if isinstance(data, str):
        return data
    for encoding in ('utf-8', 'cp1252', 'latin-1'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def run_antivirus(path):
    if not settings.ANTIVIRUS_ENABLED:
        if settings.REQUIRE_ANTIVIRUS:
            raise SecurityValidationError('Antimalware obrigatório, porém não está habilitado.')
        return {'executado': False, 'resultado': 'nao_configurado'}

    command = shlex.split(settings.ANTIVIRUS_COMMAND) + [str(path)]
    try:
        proc = subprocess.run(command, capture_output=True, timeout=600, check=False)
        stderr = _decode_tool_output(proc.stderr)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        if settings.REQUIRE_ANTIVIRUS:
            raise SecurityValidationError(f'Falha ao executar antimalware: {exc}') from exc
        return {'executado': False, 'resultado': 'indisponivel', 'detalhe': str(exc)}

    if proc.returncode == 1:
        raise SecurityValidationError('O mecanismo antimalware detectou conteúdo malicioso.')
    if proc.returncode not in (0,):
        if settings.REQUIRE_ANTIVIRUS:
            raise SecurityValidationError('O mecanismo antimalware não concluiu a análise com segurança.')
        return {'executado': True, 'resultado': 'erro', 'detalhe': stderr[-2000:]}
    return {'executado': True, 'resultado': 'limpo'}
