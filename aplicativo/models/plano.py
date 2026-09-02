from decimal import Decimal

from django.db import models


class PlanoComercial(models.Model):
    class NivelAcesso(models.TextChoices):
        BASICO = 'BASICO', 'Básico'
        TOTAL = 'TOTAL', 'Total'

    nome = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=90, unique=True)
    subtitulo = models.CharField(max_length=150, blank=True)
    descricao = models.TextField(blank=True)
    nivel_acesso = models.CharField(
        max_length=10,
        choices=NivelAcesso.choices,
        default=NivelAcesso.BASICO,
        db_index=True,
        help_text='Define o nível técnico liberado no mapa. Planos comerciais diferentes podem compartilhar o mesmo nível.',
    )
    preco_mensal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    preco_anual = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    recursos = models.TextField(
        blank=True,
        help_text='Informe um recurso por linha. A lista será exibida automaticamente na Home e na tela de planos.',
    )
    recursos_exclusivos = models.TextField(
        blank=True,
        help_text='Opcional. Informe um recurso exclusivo por linha.',
    )
    destaque = models.BooleanField(default=False)
    selo = models.CharField(max_length=60, blank=True, help_text='Ex.: Melhor custo-benefício')
    texto_cta = models.CharField(max_length=50, default='Escolher plano')
    ativo = models.BooleanField(default=True, db_index=True)
    ordem = models.PositiveSmallIntegerField(default=10, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'plano comercial'
        verbose_name_plural = 'planos comerciais'
        ordering = ['ordem', 'preco_mensal', 'nome']

    def __str__(self):
        return self.nome

    @staticmethod
    def _linhas(texto):
        return [linha.strip() for linha in (texto or '').splitlines() if linha.strip()]

    @property
    def lista_recursos(self):
        return self._linhas(self.recursos)

    @property
    def lista_recursos_exclusivos(self):
        return self._linhas(self.recursos_exclusivos)

    @property
    def mensal_equivalente_anual(self):
        if self.preco_anual:
            return (self.preco_anual / Decimal('12')).quantize(Decimal('0.01'))
        return self.preco_mensal

    @property
    def valor_anual_exibicao(self):
        if self.preco_anual:
            return self.preco_anual
        return (self.preco_mensal * Decimal('12')).quantize(Decimal('0.01'))
