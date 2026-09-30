from django import forms

from billing.models import Cupom


class CupomForm(forms.ModelForm):
    class Meta:
        model = Cupom
        fields = [
            'codigo', 'descricao', 'tipo_desconto', 'valor_desconto', 'ativo',
            'validade_inicio', 'validade_fim', 'limite_total_usos',
            'aplica_mensal', 'aplica_anual',
        ]
        widgets = {
            'validade_inicio': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'validade_fim': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        }

    def clean_codigo(self):
        codigo = self.cleaned_data['codigo'].strip().upper()
        qs = Cupom.objects.filter(codigo__iexact=codigo)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('Já existe um cupom com esse código.')
        return codigo

    def clean_valor_desconto(self):
        valor = self.cleaned_data['valor_desconto']
        tipo = self.data.get('tipo_desconto')
        if valor <= 0:
            raise forms.ValidationError('O desconto deve ser maior que zero.')
        if tipo == Cupom.TipoDesconto.PERCENTUAL and valor > 100:
            raise forms.ValidationError('O percentual máximo é 100%.')
        return valor

    def clean(self):
        cleaned = super().clean()
        inicio, fim = cleaned.get('validade_inicio'), cleaned.get('validade_fim')
        if inicio and fim and fim < inicio:
            self.add_error('validade_fim', 'A validade final precisa ser posterior à inicial.')
        if self.instance.pk and cleaned.get('limite_total_usos') is not None and cleaned['limite_total_usos'] < self.instance.quantidade_usos:
            self.add_error('limite_total_usos', 'O limite não pode ser inferior aos usos já contabilizados.')
        return cleaned
