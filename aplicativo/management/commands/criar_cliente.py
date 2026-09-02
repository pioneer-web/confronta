from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction

from administracao.models import User
from aplicativo.models import PerfilCliente, PlanoComercial
from aplicativo.validators import CpfInvalido, validar_cpf


class Command(BaseCommand):
    help = 'Cria ou atualiza cliente somente com um plano comercial ativo já contratado.'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True)
        parser.add_argument('--senha', required=True)
        parser.add_argument(
            '--plano-comercial',
            required=True,
            help='Slug de um PlanoComercial ativo já contratado pelo cliente.',
        )
        parser.add_argument('--nome', default='')
        parser.add_argument('--sobrenome', default='')
        parser.add_argument('--cpf', default='')

    @transaction.atomic
    def handle(self, *args, **options):
        email = options['email'].lower().strip()
        senha = options['senha']
        slug_plano = options['plano_comercial'].strip()
        cpf = None

        try:
            plano_comercial = PlanoComercial.objects.get(slug=slug_plano, ativo=True)
        except PlanoComercial.DoesNotExist as exc:
            raise CommandError('Informe o slug de um plano comercial ativo e já contratado.') from exc

        if options['cpf'].strip():
            try:
                cpf = validar_cpf(options['cpf'])
            except CpfInvalido as exc:
                raise CommandError(str(exc)) from exc

        if not email:
            raise CommandError('Informe um e-mail válido.')
        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError('Informe um e-mail válido.') from exc

        existing = User.objects.filter(email=email).first()
        if existing and (existing.is_superuser or existing.role):
            raise CommandError('O e-mail informado pertence a uma conta administrativa e não será convertido em cliente.')

        if cpf:
            conflito = PerfilCliente.objects.filter(cpf=cpf).exclude(usuario=existing).exists() if existing else PerfilCliente.objects.filter(cpf=cpf).exists()
            if conflito:
                raise CommandError('O CPF informado já pertence a outro cliente.')

        validate_password(senha, user=existing)

        if existing:
            user = existing
            user.first_name = options['nome'].strip()
            user.last_name = options['sobrenome'].strip()
            user.is_active = True
            user.is_staff = False
            user.is_superuser = False
            user.role = None
            user.set_password(senha)
            user.save()
        else:
            user = User.objects.create_user(
                email=email,
                password=senha,
                first_name=options['nome'].strip(),
                last_name=options['sobrenome'].strip(),
                role=None,
                is_active=True,
                is_staff=False,
            )

        defaults = {
            'plano': plano_comercial.nivel_acesso,
            'plano_comercial': plano_comercial,
            'plano_desejado': None,
            'plano_desejado_comercial': None,
            'ativo': True,
        }
        if cpf:
            defaults['cpf'] = cpf
        perfil, _ = PerfilCliente.objects.update_or_create(
            usuario=user,
            defaults=defaults,
        )
        self.stdout.write(self.style.SUCCESS(
            f'Cliente {user.email} criado/atualizado no plano {perfil.nome_plano_atual}.'
        ))
