from .dataset_specs.base import DatasetSpec, FieldSpec
from .dataset_specs.sicar import SICAR_DATASETS
from .dataset_specs.ibama import IBAMA_DATASETS
from .dataset_specs.icmbio import ICMBIO_DATASETS
from .dataset_specs.cnuc import CNUC_DATASETS
from .dataset_specs.prodes import PRODES_DATASETS
from .dataset_specs.incra import INCRA_DATASETS
from .dataset_specs.sicor import SICOR_DATASETS
from .dataset_specs.funai import FUNAI_DATASETS
from .dataset_specs.publicas_flexiveis import PUBLIC_FLEX_DATASETS

DATASETS = (
    *SICAR_DATASETS, *IBAMA_DATASETS, *ICMBIO_DATASETS, *CNUC_DATASETS, *PRODES_DATASETS, *INCRA_DATASETS, *SICOR_DATASETS, *FUNAI_DATASETS, *PUBLIC_FLEX_DATASETS,
)
DATASET_BY_SLUG = {d.slug: d for d in DATASETS}

def get_dataset(slug: str):
    return DATASET_BY_SLUG.get(slug)

def datasets_for_source(fonte_slug: str) -> list[DatasetSpec]:
    return [d for d in DATASETS if d.fonte_slug == fonte_slug]

def source_groups(fonte_slug: str):
    ordered=[]
    for spec in datasets_for_source(fonte_slug):
        if spec.grupo not in ordered: ordered.append(spec.grupo)
    return [(g,[d for d in datasets_for_source(fonte_slug) if d.grupo==g]) for g in ordered]
