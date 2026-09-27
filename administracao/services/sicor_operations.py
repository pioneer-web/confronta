import re
import csv
import gzip
import io
from pathlib import Path
import zlib



IMPORTER_NOT_CONFIGURED = 'Importador de Operações SICOR ainda não configurado.'
INVALID_OPERATIONS_FILENAME = (
    'Use SICOR_OPERACAO_BASICA_ESTADO_<ANO> com ou sem extensão .gz/.csv, com ano entre 2013 e 2026.'
)
INVALID_OPERATIONS_HEADER = (
    'Cabeçalho inválido para Operações SICOR: esperado o cabeçalho oficial com 47 colunas '
    'separadas por ponto e vírgula.'
)
_OPERATIONS_HEADER = (
    '#REF_BACEN', 'NU_ORDEM', 'CNPJ_IF', 'DT_EMISSAO', 'DT_VENCIMENTO',
    'CD_INST_CREDITO', 'CD_CATEG_EMITENTE', 'CD_FONTE_RECURSO', 'CNPJ_AGENTE_INVEST',
    'CD_ESTADO', 'CD_REF_BACEN_INVESTIMENTO', 'CD_TIPO_SEGURO', 'CD_EMPREENDIMENTO',
    'CD_PROGRAMA', 'CD_TIPO_ENCARG_FINANC', 'CD_TIPO_IRRIGACAO', 'CD_TIPO_AGRICULTURA',
    'CD_FASE_CICLO_PRODUCAO', 'CD_TIPO_CULTIVO', 'CD_TIPO_INTGR_CONSOR', 'CD_TIPO_GRAO_SEMENTE',
    'VL_ALIQ_PROAGRO', 'VL_JUROS', 'VL_PRESTACAO_INVESTIMENTO', 'VL_PREV_PROD',
    'VL_QUANTIDADE', 'VL_RECEITA_BRUTA_ESPERADA', 'VL_PARC_CREDITO', 'VL_REC_PROPRIO',
    'VL_PERC_RISCO_STN', 'VL_PERC_RISCO_FUNDO_CONST', 'VL_REC_PROPRIO_SRV', 'VL_AREA_FINANC',
    'CD_SUBPROGRAMA', 'VL_PRODUTIV_OBTIDA', 'DT_FIM_COLHEITA', 'DT_FIM_PLANTIO',
    'DT_INIC_COLHEITA', 'DT_INIC_PLANTIO', 'VL_JUROS_ENC_FINAN_POSFIX',
    'VL_PERC_CUSTO_EFET_TOTAL', 'CD_CONTRATO_STN', 'CD_CNPJ_CADASTRANTE', 'VL_AREA_INFORMADA',
    'CD_CICLO_CULTIVAR', 'CD_TIPO_SOLO', 'PC_BONUS_CAR',
)
_FILENAME_RE = re.compile(
    r'^SICOR_OPERACAO_BASICA_ESTADO_(20\d{2})(?:\.(?:gz|csv))?$',
    re.IGNORECASE,
)


class SicorOperationsValidationError(ValueError):
    """Friendly validation failure for the not-yet-imported Operations dataset."""


def operations_reference_year(filename):
    """Extrai o ano apenas do padrão oficial previsto para Operações SICOR."""
    match = _FILENAME_RE.fullmatch(str(filename or '').rsplit('/', 1)[-1].rsplit('\\', 1)[-1])
    if not match:
        return None
    year = int(match.group(1))
    if not 2013 <= year <= 2026:
        return None
    return year


def validate_operations_filename(filename):
    year = operations_reference_year(filename)
    if year is None:
        raise ValueError(INVALID_OPERATIONS_FILENAME)
    return year


def validate_operations_header(source, filename):
    """Read only the first record, streaming gzip when applicable."""
    filename = Path(str(filename or '')).name
    if operations_reference_year(filename) is None:
        raise SicorOperationsValidationError(INVALID_OPERATIONS_FILENAME)

    suffix = Path(filename).suffix.lower()
    if suffix not in {'', '.gz', '.csv'}:
        raise SicorOperationsValidationError(INVALID_OPERATIONS_FILENAME)

    is_path = isinstance(source, (str, Path))
    try:
        if is_path:
            opener = gzip.open if suffix == '.gz' else open
            with opener(source, 'rb') as stream:
                header_bytes = stream.readline(64 * 1024 + 1)
        else:
            stream = getattr(source, 'file', source)
            try:
                stream.seek(0)
                if suffix == '.gz':
                    with gzip.GzipFile(fileobj=stream, mode='rb') as zipped:
                        header_bytes = zipped.readline(64 * 1024 + 1)
                else:
                    header_bytes = stream.readline(64 * 1024 + 1)
            finally:
                stream.seek(0)
        if len(header_bytes) > 64 * 1024 or not header_bytes:
            raise SicorOperationsValidationError(INVALID_OPERATIONS_HEADER)
        header_text = header_bytes.decode('utf-8-sig')
        columns = next(csv.reader(io.StringIO(header_text), delimiter=';'))
    except SicorOperationsValidationError:
        raise
    except (OSError, EOFError, UnicodeError, csv.Error, ValueError, zlib.error) as exc:
        raise SicorOperationsValidationError(INVALID_OPERATIONS_HEADER) from exc

    if len(columns) != 47 or tuple(columns) != _OPERATIONS_HEADER or columns[0] != '#REF_BACEN':
        raise SicorOperationsValidationError(INVALID_OPERATIONS_HEADER)
    return operations_reference_year(filename)
