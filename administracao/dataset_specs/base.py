from dataclasses import dataclass, field
from typing import Iterable
from administracao.constants import FonteDados


@dataclass(frozen=True)
class FieldSpec:
    canonical: str
    aliases: tuple[str, ...]
    sql_type: str = 'text'
    required: bool = False


@dataclass(frozen=True)
class DatasetSpec:
    slug: str
    fonte: str
    fonte_slug: str
    label: str
    grupo: str
    stable_table: str
    raw_table: str
    name_tokens: tuple[str, ...]
    geometry_families: tuple[str, ...]
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)
    identity_required: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    identity_signals: tuple[str, ...] = field(default_factory=tuple)
    fixed_values: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    mode: str = 'replace_table'
    filename_patterns: tuple[str, ...] = field(default_factory=tuple)
    data_kind: str = 'spatial'
    year_partitioned: bool = False
    geometry_wkt_field: str = ''
    geometry_srid: int | None = None


def F(canonical, *aliases, sql_type='text', required=False):
    values = (canonical,) + tuple(aliases)
    return FieldSpec(canonical, values, sql_type, required)
