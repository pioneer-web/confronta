from django.test import SimpleTestCase

from administracao.constants import BATCH_FONTE_SLUGS, FONTE_SLUGS
from administracao.services.batch import allowed_input_extensions, _classify_batch_input
from administracao.datasets import get_dataset


class AllSourcesBatchTests(SimpleTestCase):
    def test_all_registered_sources_are_available_in_batch(self):
        self.assertEqual(set(BATCH_FONTE_SLUGS), set(FONTE_SLUGS))

    def test_source_extension_policies(self):
        self.assertEqual(allowed_input_extensions('sicar'), {'.zip', '.gpkg'})
        self.assertEqual(allowed_input_extensions('sicor'), {'.gz', '.csv'})
        self.assertTrue({'.csv', '.gz', '.zip'}.issubset(allowed_input_extensions('sncr')))
        self.assertTrue({'.zip', '.gpkg', '.geojson'}.issubset(allowed_input_extensions('sigef')))

    def test_single_profile_source_is_classified_without_guessing(self):
        spec, report = _classify_batch_input('/tmp/qualquer_nome.gpkg', 'sigef')
        self.assertEqual(spec.slug, 'sigef-parcelas')
        self.assertEqual(report['criterio'], 'PERFIL_UNICO_DA_FONTE')

    def test_sicor_filename_selects_registered_profile(self):
        spec, report = _classify_batch_input('/tmp/SICOR_PROPRIEDADES.gz', 'sicor')
        self.assertEqual(spec.slug, 'sicor-propriedades')
        self.assertEqual(report['criterio'], 'NOME_OFICIAL_SICOR')


    def test_sicor_glebas_wkt_filename_selects_wkt_profile(self):
        spec, report = _classify_batch_input('/tmp/sicor_glebas_wkt_2026.gz', 'sicor')
        self.assertEqual(spec.slug, 'sicor-glebas-wkt')
        self.assertEqual(report['criterio'], 'NOME_OFICIAL_SICOR')

    def test_sicor_glebas_contrat_filename_selects_point_profile(self):
        spec, report = _classify_batch_input('/tmp/SICOR_GLEBAS_CONTRAT.gz', 'sicor')
        self.assertEqual(spec.slug, 'sicor-glebas-contratadas')
        self.assertEqual(report['criterio'], 'NOME_OFICIAL_SICOR')
