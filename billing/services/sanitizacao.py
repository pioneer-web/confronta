"""Allowlist de campos técnicos usados pelo worker; descarta dados do pagador."""

_CAMPOS_EVENTO = {
    'checkout': {'id', 'customer', 'status', 'subscription', 'payment', 'externalReference', 'installmentCount'},
    'subscription': {'id', 'customer', 'status', 'nextDueDate', 'cycle', 'value'},
    'payment': {
        'id', 'subscription', 'customer', 'status', 'billingType', 'value', 'netValue',
        'dueDate', 'confirmedDate', 'paymentDate', 'invoiceUrl', 'installment', 'installmentId', 'installmentCount',
    },
}
_CAMPOS_RESPOSTA = {'id', 'link', 'status', 'externalReference', 'expiresAt', 'installmentCount', 'installmentId', 'installment'}


def _somente_campos(dados, permitidos):
    if not isinstance(dados, dict):
        return {}
    return {
        chave: valor for chave, valor in dados.items()
        if chave in permitidos and isinstance(valor, (str, int, float, bool, type(None)))
    }


def sanitizar_evento(payload):
    if not isinstance(payload, dict):
        return {}
    limpo = {k: v for k, v in payload.items() if k in {'id', 'event', 'dateCreated'} and isinstance(v, (str, int, float, bool, type(None)))}
    for entidade, campos in _CAMPOS_EVENTO.items():
        if entidade in payload:
            entidade_limpa = _somente_campos(payload[entidade], campos)
            dados = payload.get(entidade)
            if entidade == 'checkout' and isinstance(dados, dict):
                for sub in ('subscription', 'payment'):
                    if isinstance(dados.get(sub), dict):
                        entidade_limpa[sub] = _somente_campos(dados[sub], _CAMPOS_EVENTO[sub])
            if entidade == 'payment' and isinstance(dados, dict) and isinstance(dados.get('installment'), dict):
                entidade_limpa['installment'] = _somente_campos(dados['installment'], {'id', 'status', 'value', 'installmentCount'})
            limpo[entidade] = entidade_limpa
    return limpo


def sanitizar_resposta(payload):
    if not isinstance(payload, dict):
        return {}
    limpo = _somente_campos(payload, _CAMPOS_RESPOSTA)
    if isinstance(payload.get('installment'), dict):
        limpo['installment'] = _somente_campos(payload['installment'], {'id', 'status', 'value', 'installmentCount'})
    return limpo
