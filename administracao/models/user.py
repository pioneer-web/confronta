from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('O e-mail é obrigatório.')
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields['role'] = None
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superadministrador deve possuir is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superadministrador deve possuir is_superuser=True.')
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN_TOTAL = 'ADMIN_TOTAL', 'Administrador Total'
        ADMIN_JUNIOR = 'ADMIN_JUNIOR', 'Administrador Júnior'

    username = None
    email = models.EmailField('e-mail', unique=True)
    role = models.CharField('nível administrativo', max_length=20, choices=Role.choices, null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        verbose_name = 'administrador'
        verbose_name_plural = 'administradores'
        swappable = 'AUTH_USER_MODEL'

    def save(self, *args, **kwargs):
        self.email = self.email.lower().strip()
        if self.is_superuser:
            self.role = None
        super().save(*args, **kwargs)

    @property
    def nivel_display(self):
        return 'Superadministrador' if self.is_superuser else self.get_role_display()

    @property
    def can_manage_tables(self):
        return self.is_superuser or self.role == self.Role.ADMIN_TOTAL
