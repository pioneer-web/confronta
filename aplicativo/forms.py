from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from administracao.models import User
from aplicativo.access import resolver_acesso_aplicativo
from aplicativo.models import PerfilCliente, PlanoComercial
from aplicativo.validators import CarInvalido, normalizar_car


class ClienteLoginForm(forms.Form):
    email = forms.EmailField(label='E-mail')
    password = forms.CharField(label='Senha', widget=forms.PasswordInput)

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.user_cache = None

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get('email')
        password = cleaned.get('password')
        if not email or not password:
            return cleaned

        user = authenticate(self.request, username=email.lower().strip(), password=password)
        if user is None:
            raise forms.ValidationError('E-mail ou senha inválidos.')
        if not user.is_active:
            raise forms.ValidationError('Esta conta está desativada.')

        try:
            perfil = user.perfil_cliente
        except PerfilCliente.DoesNotExist:
            perfil = None
        if perfil is not None and not perfil.ativo:
            raise forms.ValidationError('O acesso deste cliente está desativado pela administração.')

        acesso = resolver_acesso_aplicativo(user)
        if acesso is None:
            raise forms.ValidationError('Esta conta não possui acesso à Área Aplicativo.')

        self.user_cache = user
        return cleaned

    def get_user(self):
        return self.user_cache


class CadastroClienteForm(forms.Form):
    nome = forms.CharField(label='Nome completo', max_length=150)
    email = forms.EmailField(label='E-mail')
    telefone = forms.CharField(
        label='Telefone',
        max_length=25,
        widget=forms.TextInput(attrs={
            'inputmode': 'tel',
            'autocomplete': 'tel',
            'placeholder': '(00) 00000-0000',
        }),
    )
    password1 = forms.CharField(label='Senha', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirmar senha', widget=forms.PasswordInput)

    def clean_nome(self):
        nome = ' '.join(self.cleaned_data['nome'].split())
        if len(nome) < 3:
            raise forms.ValidationError('Informe seu nome completo.')
        return nome

    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('Já existe uma conta cadastrada com este e-mail.')
        return email

    def clean_telefone(self):
        telefone = ' '.join(str(self.cleaned_data.get('telefone') or '').split())
        digitos = ''.join(c for c in telefone if c.isdigit())
        if len(digitos) < 8:
            raise forms.ValidationError('Informe um telefone válido.')
        return telefone

    def clean(self):
        cleaned = super().clean()
        senha_1 = cleaned.get('password1')
        senha_2 = cleaned.get('password2')
        if senha_1 and senha_2 and senha_1 != senha_2:
            self.add_error('password2', 'As senhas não coincidem.')
            return cleaned
        if senha_1:
            try:
                validate_password(senha_1)
            except ValidationError as exc:
                self.add_error('password1', exc)
        return cleaned


class ContaClienteForm(forms.Form):
    """Edição segura dos dados básicos da conta autenticada.

    Não altera senha, papel administrativo, plano ou permissões. Clientes
    comuns também podem atualizar telefone e empresa do PerfilCliente.
    """

    nome = forms.CharField(label='Nome', max_length=150)
    email = forms.EmailField(label='E-mail')
    telefone = forms.CharField(label='Telefone', max_length=25, required=False)
    empresa = forms.CharField(label='Empresa', max_length=150, required=False)

    def __init__(self, *args, user, **kwargs):
        self.user = user
        try:
            self.perfil = user.perfil_cliente
        except PerfilCliente.DoesNotExist:
            self.perfil = None

        initial = kwargs.setdefault('initial', {})
        possui_dados = bool(args and args[0] is not None) or kwargs.get('data') is not None
        if not possui_dados:
            initial.setdefault('nome', user.get_full_name() or user.first_name or '')
            initial.setdefault('email', user.email)
            if self.perfil is not None:
                initial.setdefault('telefone', self.perfil.telefone)
                initial.setdefault('empresa', self.perfil.empresa)

        super().__init__(*args, **kwargs)

        if self.perfil is None:
            self.fields.pop('telefone')
            self.fields.pop('empresa')

    def clean_nome(self):
        nome = ' '.join(self.cleaned_data['nome'].split())
        if len(nome) < 2:
            raise forms.ValidationError('Informe seu nome.')
        return nome

    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        if User.objects.exclude(pk=self.user.pk).filter(email=email).exists():
            raise forms.ValidationError('Este e-mail já está em uso por outra conta.')
        return email

    def save(self):
        self.user.first_name = self.cleaned_data['nome']
        self.user.last_name = ''
        self.user.email = self.cleaned_data['email']
        self.user.save(update_fields=['first_name', 'last_name', 'email'])

        if self.perfil is not None:
            self.perfil.telefone = self.cleaned_data.get('telefone', '')
            self.perfil.empresa = self.cleaned_data.get('empresa', '')
            self.perfil.save(update_fields=['telefone', 'empresa', 'atualizado_em'])

        return self.user


class EscolhaPlanoForm(forms.Form):
    plano = forms.CharField(widget=forms.HiddenInput)

    def clean_plano(self):
        valor = str(self.cleaned_data['plano']).strip()
        qs = PlanoComercial.objects.filter(ativo=True)
        if valor in {PerfilCliente.Plano.BASICO, PerfilCliente.Plano.TOTAL}:
            plano = qs.filter(nivel_acesso=valor).order_by('ordem', 'pk').first()
        else:
            try:
                plano = qs.get(pk=int(valor))
            except (ValueError, TypeError, PlanoComercial.DoesNotExist):
                plano = None
        if plano is None:
            raise forms.ValidationError('O plano informado não está disponível para contratação.')
        return plano


class ConsultaCarForm(forms.Form):
    car = forms.CharField(
        label='Número do CAR',
        max_length=120,
        widget=forms.TextInput(attrs={
            'autocomplete': 'off',
            'spellcheck': 'false',
            'placeholder': '00-0000000-00000000000000000000000000000000',
        }),
    )

    def clean_car(self):
        try:
            return normalizar_car(self.cleaned_data['car'])
        except CarInvalido as exc:
            raise forms.ValidationError(str(exc)) from exc


class ConsultaCoordenadaForm(forms.Form):
    latitude = forms.FloatField(label='Latitude', min_value=-90, max_value=90)
    longitude = forms.FloatField(label='Longitude', min_value=-180, max_value=180)

    def clean(self):
        cleaned = super().clean()
        latitude = cleaned.get('latitude')
        longitude = cleaned.get('longitude')
        if latitude is None or longitude is None:
            return cleaned
        # Evita coordenada vazia/placeholder comum sem bloquear locais legítimos.
        if abs(latitude) < 1e-12 and abs(longitude) < 1e-12:
            raise forms.ValidationError('Informe uma coordenada válida para a consulta.')
        return cleaned


class ConsultaGeometriaForm(forms.Form):
    geojson = forms.CharField(label='Geometria', max_length=1_500_000)
