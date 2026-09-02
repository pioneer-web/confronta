from django.conf import settings
from django.db import models
from django.utils import timezone


class PerfilCliente(models.Model):
    class Plano(models.TextChoices):
        SEM_PLANO = 'SEM_PLANO', 'Sem plano'
        BASICO = 'BASICO', 'Básico'
        TOTAL = 'TOTAL', 'Total'

    PLANOS_CONTRATAVEIS = (
        (Plano.BASICO, 'Básico'),
        (Plano.TOTAL, 'Total'),
    )

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='perfil_cliente',
    )
    cpf = models.CharField(max_length=11, unique=True, null=True, blank=True)
    telefone = models.CharField(max_length=25, blank=True)
    empresa = models.CharField(max_length=150, blank=True)
    plano = models.CharField(
        max_length=12,
        choices=Plano.choices,
        default=Plano.SEM_PLANO,
        db_index=True,
    )
    plano_desejado = models.CharField(
        max_length=10,
        choices=PLANOS_CONTRATAVEIS,
        null=True,
        blank=True,
    )
    plano_comercial = models.ForeignKey(
        'aplicativo.PlanoComercial',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='clientes',
    )
    plano_desejado_comercial = models.ForeignKey(
        'aplicativo.PlanoComercial',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='interessados',
    )
    inicio_acesso = models.DateField(null=True, blank=True)
    fim_acesso = models.DateField(null=True, blank=True, db_index=True)
    renovacao_automatica = models.BooleanField(default=False)
    observacoes_admin = models.TextField(blank=True)
    ativo = models.BooleanField(default=True, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'perfil de cliente'
        verbose_name_plural = 'perfis de clientes'
        ordering = ['usuario__email']

    def __str__(self):
        return f'{self.usuario.email} — {self.nome_plano_atual}'

    @property
    def nome_plano_atual(self):
        if self.plano_comercial_id:
            return self.plano_comercial.nome
        return self.get_plano_display()

    @property
    def acesso_expirado(self):
        return bool(self.fim_acesso and self.fim_acesso < timezone.localdate())

    @property
    def acesso_agendado(self):
        return bool(self.inicio_acesso and self.inicio_acesso > timezone.localdate())

    @property
    def dias_restantes(self):
        if not self.fim_acesso:
            return None
        return (self.fim_acesso - timezone.localdate()).days

    @property
    def acesso_vigente(self):
        if not self.ativo or self.acesso_expirado:
            return False
        if self.acesso_agendado:
            return False
        return True

    @property
    def possui_plano(self):
        return self.acesso_vigente and self.plano in {self.Plano.BASICO, self.Plano.TOTAL}

    @property
    def pode_consultar(self):
        return self.possui_plano

    @property
    def pode_desenhar_glebas(self):
        return self.acesso_vigente and self.plano == self.Plano.TOTAL
