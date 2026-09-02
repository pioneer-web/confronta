from django.test import SimpleTestCase

from aplicativo.templatetags.aplicativo_extras import display_value, field_label


class TemplateFiltersV034Tests(SimpleTestCase):
    def test_rotulos_territoriais(self):
        self.assertEqual(field_label('numero_embargo'), 'Número do embargo')
        self.assertEqual(field_label('area_sobreposta_ha'), 'Área sobreposta (ha)')
        self.assertEqual(field_label('categoria_manejo'), 'Categoria de manejo')

    def test_formatacao_decimal(self):
        self.assertEqual(display_value(1234.5), '1.234,50')
        self.assertEqual(display_value(''), 'Não informado')
