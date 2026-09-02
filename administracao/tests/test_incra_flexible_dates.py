from django.test import SimpleTestCase

from administracao.datasets import get_dataset
from administracao.services.normalization import preferred_slash_date_order


class IncraFlexibleDatePolicyTests(SimpleTestCase):
    def test_single_mdy_outlier_does_not_block_dmy_column(self):
        self.assertEqual(preferred_slash_date_order(235, 1), 'DMY')

    def test_single_dmy_outlier_does_not_block_mdy_column(self):
        self.assertEqual(preferred_slash_date_order(1, 235), 'MDY')

    def test_truly_mixed_column_has_no_ambiguous_default(self):
        self.assertIsNone(preferred_slash_date_order(5, 4))

    def test_quilombolas_maps_all_confirmed_extra_fields(self):
        spec = get_dataset('incra-quilombolas')
        fields = {field.canonical: field for field in spec.fields}
        self.assertEqual(fields['dt_public1'].sql_type, 'date_flexible')
        self.assertEqual(fields['nr_perimet'].sql_type, 'numeric')
        self.assertIn('tp_levanta', fields)
        self.assertIn('nr_escalao', fields)
        self.assertEqual(fields['perimetro_'].sql_type, 'numeric')

    def test_quilombolas_real_2026_schema_is_fully_mapped(self):
        from administracao.services.field_matching import find_matching_field
        spec = get_dataset('incra-quilombolas')
        real_fields = [
            'cd_quilomb', 'cd_sr', 'nr_process', 'nm_comunid', 'nm_municip', 'cd_uf',
            'dt_publica', 'dt_public1', 'nr_familia', 'dt_titulac', 'nr_area_ha',
            'nr_perimet', 'cd_sipra', 'ob_descric', 'st_titulad', 'dt_decreto',
            'tp_levanta', 'nr_escalao', 'area_calc_', 'perimetro_', 'esfera', 'fase',
            'responsave',
        ]
        mapped_sources = {
            find_matching_field(real_fields, field.aliases)
            for field in spec.fields
            if find_matching_field(real_fields, field.aliases)
        }
        self.assertEqual(set(real_fields), mapped_sources)

    def test_date_sql_never_uses_datestyle_dependent_to_date(self):
        import inspect
        from administracao.services import normalization
        source = inspect.getsource(normalization._flexible_date_expr)
        self.assertNotIn('to_date(', source)
        self.assertNotIn('::date', source)
        self.assertIn('_safe_make_date', source)
    def test_safe_make_date_uses_mod_function_not_percent_operator(self):
        import inspect
        from administracao.services import normalization
        source = inspect.getsource(normalization._safe_make_date)
        self.assertIn('MOD({y}, 400)', source)
        self.assertNotIn('{y} % 400', source)
        self.assertNotIn('{y} % 4', source)
        self.assertNotIn('{y} % 100', source)

