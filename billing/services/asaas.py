import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class AsaasConfigurationError(RuntimeError):
    pass


class AsaasAPIError(RuntimeError):
    def __init__(self, message, *, status_code=None, response=None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response or {}

    @property
    def descriptions(self):
        errors = self.response.get('errors') if isinstance(self.response, dict) else None
        if not isinstance(errors, list):
            return []
        result = []
        for item in errors:
            if not isinstance(item, dict):
                continue
            description = str(item.get('description') or '').strip()
            if description:
                result.append(description)
        return result

    def __str__(self):
        base = super().__str__()
        details = self.descriptions
        if not details:
            return base
        return f"{base} {' '.join(details[:3])}"


@dataclass(frozen=True)
class AsaasClient:
    api_key: str
    base_url: str
    user_agent: str
    timeout: int = 25

    @classmethod
    def from_settings(cls):
        api_key = (getattr(settings, 'ASAAS_API_KEY', '') or '').strip()
        if not api_key:
            raise AsaasConfigurationError('ASAAS_API_KEY não configurada.')

        ambiente = (getattr(settings, 'ASAAS_ENVIRONMENT', 'sandbox') or 'sandbox').strip().lower()
        default_url = 'https://api-sandbox.asaas.com/v3' if ambiente == 'sandbox' else 'https://api.asaas.com/v3'
        return cls(
            api_key=api_key,
            base_url=(getattr(settings, 'ASAAS_API_BASE_URL', default_url) or default_url).rstrip('/'),
            user_agent=getattr(settings, 'ASAAS_USER_AGENT', 'CONFRONTA/1.0 billing'),
            timeout=int(getattr(settings, 'ASAAS_HTTP_TIMEOUT', 25)),
        )

    def request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = Request(
            f'{self.base_url}/{path.lstrip("/")}',
            data=body,
            method=method.upper(),
            headers={
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'User-Agent': self.user_agent,
                'access_token': self.api_key,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode('utf-8')
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode('utf-8', errors='replace')
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {'raw': raw}
            raise AsaasAPIError(
                f'Asaas retornou HTTP {exc.code}.',
                status_code=exc.code,
                response=parsed,
            ) from exc
        except URLError as exc:
            raise AsaasAPIError(f'Falha de conexão com o Asaas: {exc.reason}') from exc

    def criar_checkout(self, payload):
        return self.request('POST', '/checkouts', payload)

    def cancelar_checkout(self, checkout_id):
        return self.request('POST', f'/checkouts/{checkout_id}/cancel')

    def remover_assinatura(self, subscription_id):
        return self.request('DELETE', f'/subscriptions/{subscription_id}')

    def criar_webhook(self, payload):
        return self.request('POST', '/webhooks', payload)
