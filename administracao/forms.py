from pathlib import Path

from django import forms
from django.contrib.auth import authenticate
from django.utils import timezone

from administracao.constants import FONTE_SLUGS, BATCH_FONTE_SLUGS
from administracao.datasets import get_dataset, datasets_for_source
from administracao.models import User
from administracao.services.partitioning import UF_NAMES
from administracao.services.prodes_filter import DEFAULT_PRODES_START_YEAR


def _apply_tabler_form_classes(form):
    """Adiciona classes visuais do Tabler sem alterar validações ou regras de negócio."""
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.HiddenInput):
            continue
        current = widget.attrs.get('class', '').strip()
        if isinstance(widget, forms.CheckboxInput):
            css = 'form-check-input'
        else:
            css = 'form-select' if isinstance(widget, forms.Select) else 'form-control'
        widget.attrs['class'] = f'{current} {css}'.strip()


class LoginForm(forms.Form):
    email = forms.EmailField(label='E-mail')
    password = forms.CharField(label='Senha', widget=forms.PasswordInput)

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.user_cache = None
        _apply_tabler_form_classes(self)

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get('email')
        password = cleaned.get('password')
        if email and password:
            self.user_cache = authenticate(self.request, username=email.lower(), password=password)
            if self.user_cache is None:
                raise forms.ValidationError('E-mail ou senha inválidos.')
            if not self.user_cache.is_active:
                raise forms.ValidationError('Esta conta está desativada.')
            if not (self.user_cache.is_superuser or self.user_cache.role in {User.Role.ADMIN_TOTAL, User.Role.ADMIN_JUNIOR}):
                self.user_cache = None
                raise forms.ValidationError('Esta conta não possui acesso ao Manage Confronta.')
        return cleaned

    def get_user(self):
        return self.user_cache


class UploadBaseForm(forms.Form):
    arquivo = forms.FileField(label='Arquivo')
    ano_inicial = forms.IntegerField(
        label='Importar ocorrências a partir de',
        required=False,
        min_value=DEFAULT_PRODES_START_YEAR,
        help_text=f'O padrão do projeto é {DEFAULT_PRODES_START_YEAR}. Registros anteriores ao ano escolhido não serão promovidos.',
    )

    def __init__(self, *args, source_slug=None, dataset_slug=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_slug = str(source_slug or '').strip().lower()
        self.dataset_slug = str(dataset_slug or '').strip().lower()
        if self.source_slug == 'prodes':
            self.fields['ano_inicial'].initial = DEFAULT_PRODES_START_YEAR
            self.fields['ano_inicial'].widget.attrs.update({
                'min': str(DEFAULT_PRODES_START_YEAR),
                'max': str(timezone.localdate().year),
                'inputmode': 'numeric',
            })
        else:
            self.fields.pop('ano_inicial', None)
        spec = get_dataset(self.dataset_slug) if self.dataset_slug else None
        if self.source_slug == 'sicar':
            self.fields['arquivo'].widget.attrs['accept'] = '.zip,.gpkg'
            self.fields['arquivo'].label = 'Arquivo ZIP ou GPKG'
        elif self.source_slug == 'sicor':
            self.fields['arquivo'].widget.attrs['accept'] = '.gz,.csv'
            self.fields['arquivo'].label = 'Arquivo SICOR (.gz ou .csv)'
            self.fields['arquivo'].help_text = 'Envie o arquivo oficial correspondente ao perfil selecionado. O cabeçalho real será validado antes de qualquer escrita.'
        elif spec and spec.mode == 'raw_only' and spec.data_kind == 'tabular_flexible':
            self.fields['arquivo'].widget.attrs['accept'] = '.csv,.gz,.zip'
            self.fields['arquivo'].label = 'Arquivo CSV, GZIP ou ZIP'
            self.fields['arquivo'].help_text = 'Entrada manual RAW flexível. ZIP deve conter exatamente um CSV. Todos os campos recebidos serão preservados.'
        elif spec and spec.mode == 'raw_only' and spec.data_kind == 'spatial_flexible':
            self.fields['arquivo'].widget.attrs['accept'] = '.zip,.gpkg,.geojson,.json,.gml,.kml'
            self.fields['arquivo'].label = 'Arquivo vetorial'
            self.fields['arquivo'].help_text = 'Aceita ZIP, GPKG, GeoJSON, GML ou KML. O Manage valida CRS, geometria e estrutura sem inventar campos operacionais.'
        else:
            self.fields['arquivo'].widget.attrs['accept'] = '.zip'
            self.fields['arquivo'].label = 'Arquivo ZIP'
        _apply_tabler_form_classes(self)

    def clean_arquivo(self):
        arquivo = self.cleaned_data['arquivo']
        suffix = Path(arquivo.name).suffix.lower()
        spec = get_dataset(self.dataset_slug) if self.dataset_slug else None
        if self.source_slug == 'sicar':
            allowed = {'.zip', '.gpkg'}
            expected = '.zip ou .gpkg'
        elif self.source_slug == 'sicor':
            allowed = {'.gz', '.csv'}
            expected = '.gz ou .csv'
        elif spec and spec.mode == 'raw_only' and spec.data_kind == 'tabular_flexible':
            allowed = {'.csv', '.gz', '.zip'}
            expected = '.csv, .gz ou .zip'
        elif spec and spec.mode == 'raw_only' and spec.data_kind == 'spatial_flexible':
            allowed = {'.zip', '.gpkg', '.geojson', '.json', '.gml', '.kml'}
            expected = '.zip, .gpkg, .geojson, .json, .gml ou .kml'
        else:
            allowed = {'.zip'}
            expected = '.zip'
        if suffix not in allowed:
            raise forms.ValidationError(f'Envie um arquivo com extensão {expected}.')
        return arquivo

    def clean_ano_inicial(self):
        if self.source_slug != 'prodes':
            return None
        value = self.cleaned_data.get('ano_inicial')
        year = int(value or DEFAULT_PRODES_START_YEAR)
        current_year = timezone.localdate().year
        if year > current_year:
            raise forms.ValidationError(f'O ano inicial não pode ser posterior a {current_year}.')
        return year


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)] if data else []


def _batch_allowed_extensions(source_slug):
    source_slug = str(source_slug or '').strip().lower()
    if source_slug == 'sicar':
        return {'.zip', '.gpkg'}
    allowed = set()
    for spec in datasets_for_source(source_slug):
        if spec.data_kind in {'sicor_csv', 'sicor_wkt', 'sicor_gleba_points'}:
            allowed.update({'.gz', '.csv'})
        elif spec.data_kind == 'tabular_flexible':
            allowed.update({'.csv', '.gz', '.zip'})
        elif spec.data_kind == 'spatial_flexible':
            allowed.update({'.zip', '.gpkg', '.geojson', '.json', '.gml', '.kml'})
        else:
            allowed.add('.zip')
    return allowed or {'.zip'}


def _batch_accept(source_slug):
    return ','.join(sorted(_batch_allowed_extensions(source_slug)))


class ImportacaoLoteForm(forms.Form):
    fonte = forms.ChoiceField(
        label='Fonte',
        choices=[(slug, value.label) for slug, value in BATCH_FONTE_SLUGS.items()],
    )
    ano_inicial = forms.IntegerField(
        label='Importar ocorrências a partir de',
        required=False,
        min_value=DEFAULT_PRODES_START_YEAR,
        help_text=f'Aplicado somente ao PRODES. Padrão: {DEFAULT_PRODES_START_YEAR}.',
    )
    uf = forms.ChoiceField(
        label='Estado (opcional)', required=False,
        choices=[('', 'Detectar automaticamente')] + [(uf, f'{uf} — {nome}') for uf, nome in sorted(UF_NAMES.items())],
        help_text='Use quando todos os arquivos deste lote pertencem à mesma UF. Em um lote nacional, deixe em detectar automaticamente.',
    )
    arquivos = MultipleFileField(
        label='Arquivos do lote', required=False,
        help_text='Selecione os arquivos oficiais da fonte.',
        widget=MultipleFileInput(attrs={'accept': '.zip'}),
    )


    def __init__(self, *args, fonte_locked=None, uf_locked=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fonte_locked = fonte_locked if fonte_locked in BATCH_FONTE_SLUGS else None
        self.uf_locked = str(uf_locked or '').strip().upper() if str(uf_locked or '').strip().upper() in UF_NAMES else ''
        if self.fonte_locked:
            self.fields['fonte'].initial = self.fonte_locked
            self.fields['fonte'].widget = forms.HiddenInput()
        if self.fonte_locked == 'prodes':
            self.fields['ano_inicial'].initial = DEFAULT_PRODES_START_YEAR
            self.fields['ano_inicial'].widget.attrs.update({
                'min': str(DEFAULT_PRODES_START_YEAR),
                'max': str(timezone.localdate().year),
                'inputmode': 'numeric',
            })
        elif self.fonte_locked:
            self.fields.pop('ano_inicial', None)
        if self.fonte_locked and self.fonte_locked != 'sicar':
            self.fields.pop('uf', None)
        elif self.fonte_locked == 'sicar' and self.uf_locked:
            self.fields['uf'].initial = self.uf_locked
            self.fields['uf'].widget = forms.HiddenInput()
        elif not self.fonte_locked:
            # No fluxo multifonte o campo permanece no formulário para poder ser
            # habilitado dinamicamente quando o administrador escolher SICAR.
            self.fields['uf'].widget.attrs['data-source-field'] = 'sicar'

        if self.fonte_locked:
            allowed = _batch_allowed_extensions(self.fonte_locked)
            self.fields['arquivos'].label = 'Arquivos do lote'
            self.fields['arquivos'].help_text = (
                'Selecione um ou mais arquivos oficiais. O navegador envia e o Manage processa um por vez. '
                f'Formatos permitidos: {", ".join(sorted(allowed))}.'
            )
            self.fields['arquivos'].widget.attrs['accept'] = _batch_accept(self.fonte_locked)
        else:
            # O JS restringe visualmente conforme a fonte; a validação do servidor
            # continua sendo a autoridade final.
            union = set()
            for source in BATCH_FONTE_SLUGS:
                union.update(_batch_allowed_extensions(source))
            self.fields['arquivos'].widget.attrs['accept'] = ','.join(sorted(union))
        _apply_tabler_form_classes(self)

    def clean_fonte(self):
        value = self.cleaned_data['fonte']
        if self.fonte_locked and value != self.fonte_locked:
            raise forms.ValidationError('A fonte desta importação está bloqueada para o fluxo selecionado.')
        return self.fonte_locked or value

    def clean_uf(self):
        fonte = self.fonte_locked or str(self.data.get('fonte') or '').strip().lower()
        if fonte != 'sicar':
            return ''
        value = str(self.cleaned_data.get('uf') or '').strip().upper()
        if self.uf_locked and value != self.uf_locked:
            raise forms.ValidationError('A UF desta importação está bloqueada para o estado selecionado.')
        return self.uf_locked or (value if value in UF_NAMES else '')

    def clean_ano_inicial(self):
        fonte = self.fonte_locked or str(self.data.get('fonte') or '').strip().lower()
        if fonte != 'prodes':
            return None
        value = self.cleaned_data.get('ano_inicial')
        year = int(value or DEFAULT_PRODES_START_YEAR)
        current_year = timezone.localdate().year
        if year > current_year:
            raise forms.ValidationError(f'O ano inicial não pode ser posterior a {current_year}.')
        return year

    def clean(self):
        cleaned = super().clean()
        arquivos = cleaned.get('arquivos') or []
        fonte = self.fonte_locked or str(cleaned.get('fonte') or self.data.get('fonte') or '').strip().lower()
        if not arquivos:
            raise forms.ValidationError('Selecione um ou mais arquivos oficiais.')
        allowed = _batch_allowed_extensions(fonte)
        for arquivo in arquivos:
            suffix = Path(arquivo.name).suffix.lower()
            if suffix not in allowed:
                expected = ', '.join(sorted(allowed))
                raise forms.ValidationError(
                    f'O arquivo {arquivo.name} não possui uma extensão permitida para esta fonte ({expected}).'
                )
        return cleaned



class DefinirUfItemLoteForm(forms.Form):
    uf = forms.ChoiceField(
        label='Estado',
        choices=[('', 'Selecione a UF')] + [(uf, f'{uf} — {nome}') for uf, nome in sorted(UF_NAMES.items())],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_tabler_form_classes(self)

# ============================================================================
# Gestão unificada — administradores, clientes e planos comerciais
# ============================================================================
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from aplicativo.models import PerfilCliente, PlanoComercial


class AdministradorCreateForm(forms.ModelForm):
    password1 = forms.CharField(label='Senha', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirmar senha', widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'role', 'is_active')
        labels = {
            'first_name': 'Nome',
            'last_name': 'Sobrenome',
            'email': 'E-mail',
            'role': 'Nível administrativo',
            'is_active': 'Conta ativa',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].required = True
        self.fields['role'].choices = User.Role.choices
        self.fields['is_active'].initial = True
        _apply_tabler_form_classes(self)

    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('Já existe uma conta cadastrada com este e-mail.')
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'As senhas não coincidem.')
        if p1:
            try:
                validate_password(p1)
            except ValidationError as exc:
                self.add_error('password1', exc)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.is_staff = True
        user.is_superuser = False
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


class AdministradorUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'role', 'is_active')
        labels = {
            'first_name': 'Nome',
            'last_name': 'Sobrenome',
            'email': 'E-mail',
            'role': 'Nível administrativo',
            'is_active': 'Conta ativa',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].required = True
        self.fields['role'].choices = User.Role.choices
        _apply_tabler_form_classes(self)

    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        if User.objects.exclude(pk=self.instance.pk).filter(email=email).exists():
            raise forms.ValidationError('Já existe uma conta cadastrada com este e-mail.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.is_staff = True
        user.is_superuser = False
        if commit:
            user.save()
        return user


class _ClienteAdminBaseForm(forms.Form):
    nome = forms.CharField(label='Nome', max_length=150)
    sobrenome = forms.CharField(label='Sobrenome', max_length=150, required=False)
    email = forms.EmailField(label='E-mail')
    telefone = forms.CharField(label='Telefone', max_length=25, required=False)
    plano_comercial = forms.ModelChoiceField(
        label='Plano comercial', queryset=PlanoComercial.objects.none(), required=True,
        empty_label='Selecione um plano',
    )
    inicio_acesso = forms.DateField(label='Início do acesso', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    fim_acesso = forms.DateField(label='Fim do acesso', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    ativo = forms.BooleanField(label='Acesso ativo', required=False, initial=True)
    renovacao_automatica = forms.BooleanField(label='Renovação automática', required=False)
    observacoes_admin = forms.CharField(label='Observações administrativas', required=False, widget=forms.Textarea(attrs={'rows': 5}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['plano_comercial'].queryset = PlanoComercial.objects.order_by('-ativo', 'ordem', 'nome')
        _apply_tabler_form_classes(self)

    def clean_email(self):
        return self.cleaned_data['email'].lower().strip()


    def clean(self):
        cleaned = super().clean()
        inicio, fim = cleaned.get('inicio_acesso'), cleaned.get('fim_acesso')
        if inicio and fim and fim < inicio:
            self.add_error('fim_acesso', 'A data final não pode ser anterior à data inicial.')
        return cleaned


class ClienteNovoAdminForm(_ClienteAdminBaseForm):
    password1 = forms.CharField(label='Senha inicial', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirmar senha', widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['plano_comercial'].queryset = PlanoComercial.objects.filter(ativo=True).order_by('ordem', 'nome')

    def clean_email(self):
        email = super().clean_email()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('Já existe uma conta cadastrada com este e-mail.')
        return email


    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'As senhas não coincidem.')
        if p1:
            try:
                validate_password(p1)
            except ValidationError as exc:
                self.add_error('password1', exc)
        return cleaned

    @transaction.atomic
    def save(self):
        plano = self.cleaned_data['plano_comercial']
        user = User.objects.create_user(
            email=self.cleaned_data['email'],
            password=self.cleaned_data['password1'],
            first_name=self.cleaned_data['nome'].strip(),
            last_name=self.cleaned_data.get('sobrenome', '').strip(),
            is_active=True,
            is_staff=False,
            role=None,
        )
        return PerfilCliente.objects.create(
            usuario=user,
            telefone=self.cleaned_data.get('telefone', '').strip(),
            plano=plano.nivel_acesso,
            plano_comercial=plano,
            inicio_acesso=self.cleaned_data.get('inicio_acesso'),
            fim_acesso=self.cleaned_data.get('fim_acesso'),
            ativo=bool(self.cleaned_data.get('ativo')),
            renovacao_automatica=bool(self.cleaned_data.get('renovacao_automatica')),
            observacoes_admin=self.cleaned_data.get('observacoes_admin', '').strip(),
        )


class ClienteAdminForm(_ClienteAdminBaseForm):
    nova_senha1 = forms.CharField(
        label='Nova senha',
        required=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Deixe em branco para manter a senha atual.',
    )
    nova_senha2 = forms.CharField(
        label='Confirmar nova senha',
        required=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    def __init__(self, *args, instance, **kwargs):
        self.instance = instance
        self.senha_alterada = False

        # A view chama ClienteAdminForm(request.POST or None, instance=perfil).
        # Em um GET isso significa args == (None,), portanto testar apenas
        # `not args` fazia o formulário parecer bound e impedia o carregamento
        # dos valores existentes. O formulário de edição deve sempre refletir
        # o banco quando não houver dados POST reais, independentemente do plano
        # ou do status da assinatura do cliente.
        data_posicional = args[0] if args else None
        data_nomeado = kwargs.get('data')
        formulario_bound = data_posicional is not None or data_nomeado is not None
        if not formulario_bound:
            kwargs.setdefault('initial', {
                'nome': instance.usuario.first_name,
                'sobrenome': instance.usuario.last_name,
                'email': instance.usuario.email,
                'telefone': instance.telefone,
                'plano_comercial': instance.plano_comercial_id,
                'inicio_acesso': instance.inicio_acesso,
                'fim_acesso': instance.fim_acesso,
                'ativo': instance.ativo,
                'renovacao_automatica': instance.renovacao_automatica,
                'observacoes_admin': instance.observacoes_admin,
            })
        super().__init__(*args, **kwargs)
        self.fields['plano_comercial'].required = False
        self.fields['plano_comercial'].empty_label = 'Sem plano'

    def clean_email(self):
        email = super().clean_email()
        if User.objects.exclude(pk=self.instance.usuario_id).filter(email=email).exists():
            raise forms.ValidationError('Já existe uma conta cadastrada com este e-mail.')
        return email

    def clean(self):
        cleaned = super().clean()
        senha_1 = cleaned.get('nova_senha1')
        senha_2 = cleaned.get('nova_senha2')
        if bool(senha_1) != bool(senha_2):
            self.add_error('nova_senha2', 'Informe e confirme a nova senha.')
            return cleaned
        if senha_1 and senha_1 != senha_2:
            self.add_error('nova_senha2', 'As senhas não coincidem.')
            return cleaned
        if senha_1:
            try:
                validate_password(senha_1, self.instance.usuario)
            except ValidationError as exc:
                self.add_error('nova_senha1', exc)
        return cleaned

    @transaction.atomic
    def save(self):
        user = self.instance.usuario
        user.first_name = self.cleaned_data['nome'].strip()
        user.last_name = self.cleaned_data.get('sobrenome', '').strip()
        user.email = self.cleaned_data['email']
        update_fields = ['first_name', 'last_name', 'email']
        nova_senha = self.cleaned_data.get('nova_senha1')
        if nova_senha:
            user.set_password(nova_senha)
            update_fields.append('password')
            self.senha_alterada = True
        user.save(update_fields=update_fields)

        plano = self.cleaned_data.get('plano_comercial')
        self.instance.telefone = self.cleaned_data.get('telefone', '').strip()
        self.instance.plano_comercial = plano
        self.instance.plano = plano.nivel_acesso if plano else PerfilCliente.Plano.SEM_PLANO
        self.instance.inicio_acesso = self.cleaned_data.get('inicio_acesso')
        self.instance.fim_acesso = self.cleaned_data.get('fim_acesso')
        self.instance.ativo = bool(self.cleaned_data.get('ativo'))
        self.instance.renovacao_automatica = bool(self.cleaned_data.get('renovacao_automatica'))
        self.instance.observacoes_admin = self.cleaned_data.get('observacoes_admin', '').strip()
        self.instance.save()
        return self.instance


class PlanoComercialForm(forms.ModelForm):
    class Meta:
        model = PlanoComercial
        fields = (
            'nome', 'slug', 'subtitulo', 'descricao', 'nivel_acesso',
            'preco_mensal', 'preco_anual', 'recursos', 'recursos_exclusivos',
            'destaque', 'selo', 'texto_cta', 'ativo', 'ordem',
        )
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 4}),
            'recursos': forms.Textarea(attrs={'rows': 7}),
            'recursos_exclusivos': forms.Textarea(attrs={'rows': 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_tabler_form_classes(self)

class ConfirmarExclusaoTabelaForm(forms.Form):
    confirmacao = forms.CharField(label='Digite o nome da tabela para confirmar')

    def __init__(self, *args, expected_table='', **kwargs):
        super().__init__(*args, **kwargs)
        self.expected_table = str(expected_table or '').strip()
        _apply_tabler_form_classes(self)

    def clean_confirmacao(self):
        value = str(self.cleaned_data.get('confirmacao') or '').strip()
        if value != self.expected_table:
            raise forms.ValidationError('A confirmação não corresponde ao nome da tabela.')
        return value


from aplicativo.models import AvisoCliente


class AvisoClienteAdminForm(forms.ModelForm):
    class Meta:
        model = AvisoCliente
        fields = ('mensagem', 'ativo')
        widgets = {'mensagem': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_tabler_form_classes(self)
