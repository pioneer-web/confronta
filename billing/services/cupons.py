from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from billing.models import AsaasCheckout, Cupom, CupomUso

CENTAVO = Decimal('0.01')


class CupomInvalido(ValueError):
    pass


def calcular_cupom(codigo, ciclo, valor_original, *, lock=False):
    codigo = (codigo or '').strip().upper()
    if not codigo:
        return None, Decimal('0.00'), valor_original
    qs = Cupom.objects
    if lock:
        qs = qs.select_for_update()
    cupom = qs.filter(codigo__iexact=codigo).first()
    agora = timezone.now()
    if cupom is None or not cupom.ativo:
        raise CupomInvalido('Cupom inexistente ou inativo.')
    if cupom.validade_inicio and agora < cupom.validade_inicio:
        raise CupomInvalido('Cupom ainda não está válido.')
    if cupom.validade_fim and agora > cupom.validade_fim:
        raise CupomInvalido('Cupom expirado.')
    if lock:
        CupomUso.objects.filter(
            cupom=cupom,
            status=CupomUso.Status.RESERVED,
            checkout__status__in=[AsaasCheckout.Status.ACTIVE, AsaasCheckout.Status.CREATING],
            checkout__expira_em__lte=agora,
        ).update(status=CupomUso.Status.RELEASED)
        AsaasCheckout.objects.filter(
            uso_cupom__cupom=cupom,
            uso_cupom__status=CupomUso.Status.RELEASED,
            status=AsaasCheckout.Status.ACTIVE,
            expira_em__lte=agora,
        ).update(status=AsaasCheckout.Status.EXPIRED)
    if ciclo == AsaasCheckout.Ciclo.MONTHLY and not cupom.aplica_mensal:
        raise CupomInvalido('Cupom não se aplica ao plano mensal.')
    if ciclo == AsaasCheckout.Ciclo.YEARLY and not cupom.aplica_anual:
        raise CupomInvalido('Cupom não se aplica ao plano anual.')
    if cupom.tipo_desconto == Cupom.TipoDesconto.PERCENTUAL:
        if not Decimal('0') < cupom.valor_desconto <= Decimal('100'):
            raise CupomInvalido('Configuração percentual do cupom inválida.')
        desconto = (valor_original * cupom.valor_desconto / Decimal('100')).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    else:
        desconto = min(cupom.valor_desconto, valor_original).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    final = max(Decimal('0'), valor_original - desconto).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    if cupom.limite_total_usos is not None:
        consumidos = max(cupom.quantidade_usos, CupomUso.objects.filter(cupom=cupom, status=CupomUso.Status.CONSUMED).count())
        reservados = CupomUso.objects.filter(cupom=cupom, status=CupomUso.Status.RESERVED).filter(
            Q(checkout__expira_em__isnull=True) | Q(checkout__expira_em__gt=agora)
        ).count()
        if consumidos + reservados >= cupom.limite_total_usos:
            raise CupomInvalido('Cupom atingiu o limite total de usos.')
    return cupom, desconto, final


@transaction.atomic
def reservar_cupom(checkout, codigo):
    if not (codigo or '').strip():
        return None
    cupom, desconto, final = calcular_cupom(codigo, checkout.ciclo, checkout.valor_original, lock=True)
    checkout.cupom = cupom.codigo
    checkout.desconto = desconto
    checkout.valor = final
    checkout.save(update_fields=['cupom', 'desconto', 'valor', 'atualizado_em'])
    CupomUso.objects.create(cupom=cupom, checkout=checkout)
    return cupom


@transaction.atomic
def consumir_cupom(checkout):
    uso = CupomUso.objects.select_for_update().select_related('cupom').filter(checkout=checkout).first()
    if uso is None or uso.status != CupomUso.Status.RESERVED:
        return False
    cupom = Cupom.objects.select_for_update().get(pk=uso.cupom_id)
    cupom.quantidade_usos += 1
    cupom.save(update_fields=['quantidade_usos', 'atualizado_em'])
    uso.status = CupomUso.Status.CONSUMED
    uso.consumido_em = timezone.now()
    uso.save(update_fields=['status', 'consumido_em'])
    return True


def liberar_cupom(checkout):
    CupomUso.objects.filter(checkout=checkout, status=CupomUso.Status.RESERVED).update(status=CupomUso.Status.RELEASED)
