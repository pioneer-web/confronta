from pathlib import Path

from django.conf import settings
from django.utils import timezone

from administracao.datasets import get_dataset
from administracao.models import CamadaImportada, SicarFingerprintCamada
from administracao.services.partitioning import normalize_uf


SICAR_SYNC_ORDER = (
    'sicar-perimetros',
    'sicar-area-consolidada',
    'sicar-area-pousio',
    'sicar-app',
    'sicar-hidrografia',
    'sicar-vegetacao-nativa',
    'sicar-reserva-legal',
    'sicar-servidao-administrativa',
    'sicar-uso-restrito',
)

SICAR_FILE_BASENAMES = {
    'sicar-perimetros': 'AREA_IMOVEL',
    'sicar-area-consolidada': 'AREA_CONSOLIDADA',
    'sicar-area-pousio': 'AREA_POUSIO',
    'sicar-app': 'APP',
    'sicar-hidrografia': 'HIDROGRAFIA',
    'sicar-vegetacao-nativa': 'VEGETACAO_NATIVA',
    'sicar-reserva-legal': 'RESERVA_LEGAL',
    'sicar-servidao-administrativa': 'SERVIDAO_ADMINISTRATIVA',
    'sicar-uso-restrito': 'USO_RESTRITO',
}


def dataset_last_validated_at(uf, dataset_slug):
    uf = normalize_uf(uf)
    fp = SicarFingerprintCamada.objects.filter(uf=uf, dataset_slug=dataset_slug).first()
    if fp:
        return fp.ultima_verificacao or fp.ultima_atualizacao
    layer = CamadaImportada.objects.filter(
        fonte='SICAR', dataset_slug=dataset_slug, status=CamadaImportada.Status.ATIVA,
    ).first()
    return layer.ultima_importacao if layer else None


def dataset_is_current_today(uf, dataset_slug, now=None):
    """Bloqueio idempotente barato antes de qualquer download.

    Se a partição operacional existe e a camada já foi validada/importada hoje,
    uma segunda verificação no mesmo dia não baixa o mesmo snapshot novamente.
    """
    uf = normalize_uf(uf)
    spec = get_dataset(dataset_slug)
    if not spec or spec.fonte_slug != 'sicar':
        return False
    when = dataset_last_validated_at(uf, dataset_slug)
    if not when:
        return False
    if timezone.localdate(when) != timezone.localdate(now or timezone.now()):
        return False
    try:
        # Import tardio evita ciclo com o módulo que monta o status visual das camadas.
        from administracao.services.sicar_tracking import sicar_partition_has_rows
        return sicar_partition_has_rows(spec, uf)
    except Exception:
        return False


def direct_source_url(dataset_slug):
    return str((getattr(settings, 'SICAR_DIRECT_GPKG_URLS', {}) or {}).get(dataset_slug) or '').strip()


def source_mode(dataset_slug, uf='PE'):
    if dataset_slug == 'sicar-perimetros':
        return 'WFS'
    if direct_source_url(dataset_slug):
        return 'DIRECT_GPKG'
    snapshot = find_inbox_snapshot(dataset_slug, uf)
    if snapshot:
        sidecar = snapshot.with_name(snapshot.name + '.meta.json')
        if sidecar.is_file():
            return 'PORTAL_ASSISTIDO'
        return 'INBOX'
    return 'PORTAL_PROTEGIDO'


def source_is_automatic(dataset_slug, uf='PE'):
    if dataset_slug == 'sicar-perimetros' or direct_source_url(dataset_slug):
        return True
    # No modo assistido o CAPTCHA é resolvido por uma pessoa no navegador, mas
    # depois que o arquivo chega à inbox todo o processamento é automático.
    return bool(find_inbox_snapshot(dataset_slug, uf))


def find_inbox_snapshot(dataset_slug, uf='PE'):
    root = Path(getattr(settings, 'SICAR_AUTO_INBOX', ''))
    if not root:
        return None
    uf = normalize_uf(uf)
    base = SICAR_FILE_BASENAMES.get(dataset_slug, '').upper()
    if not base or not root.exists():
        return None
    candidates = []
    for ext in ('*.gpkg', '*.zip'):
        for path in root.glob(ext):
            name = path.name.upper()
            if base in name and uf in name:
                candidates.append(path)
    if not candidates:
        # aceita nome oficial sem UF quando a pasta é exclusiva do piloto PE
        for ext in ('*.gpkg', '*.zip'):
            for path in root.glob(ext):
                if base in path.name.upper():
                    candidates.append(path)
    return max(candidates, key=lambda x: x.stat().st_mtime) if candidates else None


def source_description(dataset_slug, uf='PE'):
    mode = source_mode(dataset_slug, uf)
    if mode == 'WFS':
        return 'GeoServer/WFS oficial do SICAR'
    if mode == 'DIRECT_GPKG':
        return 'GeoPackage oficial por URL direta configurada'
    if mode == 'PORTAL_ASSISTIDO':
        return 'Portal SICAR em sessão assistida (CAPTCHA humano)'
    if mode == 'INBOX':
        return 'Arquivo oficial detectado automaticamente na caixa de entrada'
    return 'Portal SICAR protegido por validação humana (sem sessão assistida)'
