from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import math
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from aplicativo.models import LimiteSeguranca

logger = logging.getLogger('aplicativo.security')


@dataclass(frozen=True)
class EstadoLimite:
    permitido: bool
    repetir_em_segundos: int = 0
    tentativas: int = 0


def _hash_chave(escopo: str, valor: str) -> str:
    segredo = settings.SECRET_KEY.encode('utf-8')
    payload = f'{escopo}|{valor}'.encode('utf-8', errors='ignore')
    return hmac.new(segredo, payload, hashlib.sha256).hexdigest()


def obter_ip_cliente(request) -> str:
    """Retorna um IP validado sem confiar silenciosamente em headers de proxy."""
    candidato = request.META.get('REMOTE_ADDR', '') or ''
    if getattr(settings, 'TRUST_PROXY_HEADERS', False):
        # O Nginx de produção sobrescreve X-Real-IP com $remote_addr. Não usamos
        # X-Forwarded-For para evitar aceitar uma cadeia enviada pelo próprio cliente.
        candidato = request.META.get('HTTP_X_REAL_IP', '') or candidato
    try:
        return str(ipaddress.ip_address(candidato.strip()))
    except ValueError:
        return 'ip-invalido'


def _normalizar_identidade(valor: str) -> str:
    return (valor or '').strip().lower()[:254]


def _estado(escopo: str, valor_chave: str) -> EstadoLimite:
    agora = timezone.now()
    chave_hash = _hash_chave(escopo, valor_chave)
    registro = LimiteSeguranca.objects.filter(escopo=escopo, chave_hash=chave_hash).only(
        'tentativas', 'bloqueado_ate'
    ).first()
    if not registro or not registro.bloqueado_ate or registro.bloqueado_ate <= agora:
        return EstadoLimite(True, 0, registro.tentativas if registro else 0)
    restante = max(1, math.ceil((registro.bloqueado_ate - agora).total_seconds()))
    return EstadoLimite(False, restante, registro.tentativas)


def _registrar(
    escopo: str,
    valor_chave: str,
    *,
    limite: int,
    janela_segundos: int,
    bloqueio_segundos: int,
    bloquear_ao_atingir: bool,
) -> EstadoLimite:
    # Valores <= 0 desabilitam explicitamente uma política sem criar registros
    # inconsistentes. Janela/bloqueio recebem piso de 1s para evitar loops.
    limite = int(limite)
    if limite <= 0:
        return EstadoLimite(True)
    janela_segundos = max(1, int(janela_segundos))
    bloqueio_segundos = max(1, int(bloqueio_segundos))

    agora = timezone.now()
    chave_hash = _hash_chave(escopo, valor_chave)

    with transaction.atomic():
        registro = LimiteSeguranca.objects.select_for_update().filter(
            escopo=escopo,
            chave_hash=chave_hash,
        ).first()
        if registro is None:
            try:
                # Savepoint interno: se duas requisições criarem a mesma chave
                # simultaneamente, a UNIQUE resolve a corrida sem quebrar a
                # transação externa usada para o select_for_update.
                with transaction.atomic():
                    registro = LimiteSeguranca.objects.create(
                        escopo=escopo,
                        chave_hash=chave_hash,
                        janela_iniciada_em=agora,
                        tentativas=0,
                    )
            except IntegrityError:
                registro = LimiteSeguranca.objects.select_for_update().get(
                    escopo=escopo,
                    chave_hash=chave_hash,
                )

        if registro.bloqueado_ate and registro.bloqueado_ate > agora:
            restante = max(1, math.ceil((registro.bloqueado_ate - agora).total_seconds()))
            return EstadoLimite(False, restante, registro.tentativas)

        if (agora - registro.janela_iniciada_em).total_seconds() >= janela_segundos:
            registro.janela_iniciada_em = agora
            registro.tentativas = 0
            registro.bloqueado_ate = None

        registro.tentativas += 1
        excedeu = registro.tentativas >= limite if bloquear_ao_atingir else registro.tentativas > limite
        if excedeu:
            registro.bloqueado_ate = agora + timedelta(seconds=bloqueio_segundos)

        registro.save(update_fields=['janela_iniciada_em', 'tentativas', 'bloqueado_ate', 'atualizado_em'])

        if registro.bloqueado_ate and registro.bloqueado_ate > agora:
            restante = max(1, math.ceil((registro.bloqueado_ate - agora).total_seconds()))
            return EstadoLimite(False, restante, registro.tentativas)
        return EstadoLimite(True, 0, registro.tentativas)


def _limpar(escopo: str, valor_chave: str) -> None:
    LimiteSeguranca.objects.filter(
        escopo=escopo,
        chave_hash=_hash_chave(escopo, valor_chave),
    ).delete()


def verificar_login(request, email: str, *, administrativo: bool = False) -> EstadoLimite:
    ip = obter_ip_cliente(request)
    identidade = _normalizar_identidade(email) or 'sem-email'
    prefixo = 'ADMIN' if administrativo else 'CLIENTE'
    combinada = f'{ip}|{identidade}'

    for escopo, chave in (
        (f'BOT_LOGIN_{prefixo}', ip),
        (f'LOGIN_{prefixo}_COMBO', combinada),
        (f'LOGIN_{prefixo}_IDENTIDADE', identidade),
        (f'LOGIN_{prefixo}_IP', ip),
    ):
        estado = _estado(escopo, chave)
        if not estado.permitido:
            return estado
    return EstadoLimite(True)


def registrar_falha_login(request, email: str, *, administrativo: bool = False) -> EstadoLimite:
    ip = obter_ip_cliente(request)
    identidade = _normalizar_identidade(email) or 'sem-email'
    prefixo = 'ADMIN' if administrativo else 'CLIENTE'
    combinada = f'{ip}|{identidade}'

    combo = _registrar(
        f'LOGIN_{prefixo}_COMBO',
        combinada,
        limite=getattr(settings, 'LOGIN_FAILURE_LIMIT', 5),
        janela_segundos=getattr(settings, 'LOGIN_FAILURE_WINDOW_SECONDS', 900),
        bloqueio_segundos=getattr(settings, 'LOGIN_BLOCK_SECONDS', 900),
        bloquear_ao_atingir=True,
    )
    identidade_estado = _registrar(
        f'LOGIN_{prefixo}_IDENTIDADE',
        identidade,
        limite=(getattr(settings, 'ADMIN_LOGIN_IDENTITY_FAILURE_LIMIT', 8) if administrativo else getattr(settings, 'CLIENT_LOGIN_IDENTITY_FAILURE_LIMIT', 10)),
        janela_segundos=getattr(settings, 'LOGIN_FAILURE_WINDOW_SECONDS', 900),
        bloqueio_segundos=getattr(settings, 'LOGIN_IDENTITY_BLOCK_SECONDS', 900),
        bloquear_ao_atingir=True,
    )
    ip_estado = _registrar(
        f'LOGIN_{prefixo}_IP',
        ip,
        limite=(getattr(settings, 'ADMIN_LOGIN_IP_FAILURE_LIMIT', 20) if administrativo else getattr(settings, 'CLIENT_LOGIN_IP_FAILURE_LIMIT', 40)),
        janela_segundos=getattr(settings, 'LOGIN_FAILURE_WINDOW_SECONDS', 900),
        bloqueio_segundos=getattr(settings, 'LOGIN_IP_BLOCK_SECONDS', 1800),
        bloquear_ao_atingir=True,
    )
    estado = combo if not combo.permitido else identidade_estado if not identidade_estado.permitido else ip_estado
    if not estado.permitido:
        logger.warning('Bloqueio de login acionado: escopo=%s ip_hash=%s', prefixo, _hash_chave('LOG_IP', ip)[:12])
    return estado


def limpar_falhas_login(request, email: str, *, administrativo: bool = False) -> None:
    ip = obter_ip_cliente(request)
    identidade = _normalizar_identidade(email) or 'sem-email'
    prefixo = 'ADMIN' if administrativo else 'CLIENTE'
    # Limpamos o par e a identidade após autenticação legítima. O contador global
    # do IP permanece para que um login válido não apague tentativas contra várias contas.
    _limpar(f'LOGIN_{prefixo}_COMBO', f'{ip}|{identidade}')
    _limpar(f'LOGIN_{prefixo}_IDENTIDADE', identidade)


def registrar_sinal_bot(request, *, origem: str) -> None:
    ip = obter_ip_cliente(request)
    _registrar(
        f'BOT_{origem.upper()}',
        ip,
        limite=1,
        janela_segundos=3600,
        bloqueio_segundos=getattr(settings, 'BOT_BLOCK_SECONDS', 3600),
        bloquear_ao_atingir=True,
    )
    logger.warning('Sinal de automação detectado: origem=%s ip_hash=%s', origem, _hash_chave('LOG_IP', ip)[:12])


def consumir_limite_selecao_car(request) -> EstadoLimite:
    """Limita a validação leve do identificador CAR enviada por POST."""
    ip = obter_ip_cliente(request)
    user_id = str(getattr(request.user, 'pk', 'anonimo'))
    politicas = (
        ('CAR_SELECAO_USUARIO_5M', user_id, getattr(settings, 'CAR_LOOKUP_RATE_LIMIT_5MIN', 60), 300, 300),
        ('CAR_SELECAO_IP_5M', ip, getattr(settings, 'CAR_LOOKUP_IP_RATE_LIMIT_5MIN', 120), 300, 300),
    )
    for escopo, chave, limite, janela, bloqueio in politicas:
        estado = _registrar(
            escopo, chave, limite=limite, janela_segundos=janela,
            bloqueio_segundos=bloqueio, bloquear_ao_atingir=False,
        )
        if not estado.permitido:
            logger.warning('Limite de seleção CAR acionado: escopo=%s user_id=%s', escopo, user_id)
            return estado
    return EstadoLimite(True)


def consumir_limite_consulta_car(request) -> EstadoLimite:
    """Limita cada execução completa do confronto territorial (inclusive refresh)."""
    ip = obter_ip_cliente(request)
    user_id = str(getattr(request.user, 'pk', 'anonimo'))

    politicas = (
        ('CAR_USUARIO_5M', user_id, getattr(settings, 'CAR_RATE_LIMIT_5MIN', 30), 300, 300),
        ('CAR_USUARIO_1H', user_id, getattr(settings, 'CAR_RATE_LIMIT_HOUR', 120), 3600, 900),
        ('CAR_IP_5M', ip, getattr(settings, 'CAR_IP_RATE_LIMIT_5MIN', 60), 300, 300),
    )
    for escopo, chave, limite, janela, bloqueio in politicas:
        estado = _registrar(
            escopo,
            chave,
            limite=limite,
            janela_segundos=janela,
            bloqueio_segundos=bloqueio,
            bloquear_ao_atingir=False,
        )
        if not estado.permitido:
            logger.warning('Limite de consulta CAR acionado: escopo=%s user_id=%s', escopo, user_id)
            return estado
    return EstadoLimite(True)


def consumir_limite_exportacao(request) -> EstadoLimite:
    ip = obter_ip_cliente(request)
    user_id = str(getattr(request.user, 'pk', 'anonimo'))
    politicas = (
        ('EXPORT_USUARIO_1H', user_id, getattr(settings, 'EXPORT_RATE_LIMIT_HOUR', 60), 3600, 900),
        ('EXPORT_IP_1H', ip, getattr(settings, 'EXPORT_IP_RATE_LIMIT_HOUR', 120), 3600, 900),
    )
    for escopo, chave, limite, janela, bloqueio in politicas:
        estado = _registrar(
            escopo, chave, limite=limite, janela_segundos=janela,
            bloqueio_segundos=bloqueio, bloquear_ao_atingir=False,
        )
        if not estado.permitido:
            logger.warning('Limite de exportação acionado: escopo=%s user_id=%s', escopo, user_id)
            return estado
    return EstadoLimite(True)


def minutos_para_mensagem(segundos: int) -> int:
    return max(1, math.ceil(segundos / 60))
