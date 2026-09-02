"""Perfis de entrada manual flexível para bases públicas ainda sem modelo operacional fechado.

A regra é deliberadamente conservadora: o Manage recebe, valida e preserva a
estrutura recebida na RAW. Não inventamos campos canônicos nem regras de negócio.
Quando uma amostra oficial for validada, o mesmo dataset pode ganhar um perfil
operacional sem perder o histórico RAW já importado.
"""
from .base import DatasetSpec
from administracao.constants import FonteDados


def _spatial(slug, fonte, fonte_slug, label, group, table, tokens):
    return DatasetSpec(
        slug, fonte, fonte_slug, label, group,
        table, table,
        tuple(tokens), ('polygon', 'line', 'point'),
        fields=(), identity_required=(), identity_signals=(), fixed_values=(),
        mode='raw_only', filename_patterns=(), data_kind='spatial_flexible',
    )


def _tabular(slug, fonte, fonte_slug, label, group, table, tokens):
    return DatasetSpec(
        slug, fonte, fonte_slug, label, group,
        table, table,
        tuple(tokens), (),
        fields=(), identity_required=(), identity_signals=(), fixed_values=(),
        mode='raw_only', filename_patterns=(), data_kind='tabular_flexible',
    )


PUBLIC_FLEX_DATASETS = (
    _spatial(
        'sigef-parcelas', FonteDados.SIGEF, 'sigef',
        'Parcelas / acervo SIGEF', 'SIGEF', 'raw_sigef_parcelas',
        ('sigef', 'parcela', 'parcelas', 'certificada', 'certificadas'),
    ),
    _tabular(
        'sncr-dados-abertos', FonteDados.SNCR, 'sncr',
        'Dados abertos SNCR', 'SNCR', 'raw_sncr_dados_abertos',
        ('sncr', 'imovel', 'imoveis', 'cnir'),
    ),
)
