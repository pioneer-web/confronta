from django.test import SimpleTestCase

from administracao.services.batch import _preclassify_input_name


class BatchPreclassificationV33Tests(SimpleTestCase):
    def assertDataset(self, source, filename, expected):
        spec, report = _preclassify_input_name(source, filename)
        self.assertIsNotNone(spec, report)
        self.assertEqual(spec.slug, expected)

    def test_ibama_known_file(self):
        self.assertDataset('ibama', 'adm_embargo_ibama_a.shp.zip', 'ibama-termos-embargo')

    def test_icmbio_embargo_known_file(self):
        self.assertDataset('icmbio', 'embargos_icmbio_shp.zip', 'icmbio-areas-embargadas')

    def test_incra_quilombolas_known_file(self):
        self.assertDataset('incra', 'Áreas de Quilombolas.zip', 'incra-quilombolas')

    def test_sncr_known_file(self):
        self.assertDataset('sncr', 'Imoveis_PE_01_08_2026.csv', 'sncr-dados-abertos')

    def test_funai_known_file(self):
        self.assertDataset('funai', 'tis_poligonais.zip', 'funai-terras-indigenas')

    def test_sicor_wkt_known_file(self):
        self.assertDataset('sicor', 'sicor_glebas_wkt_2026.gz', 'sicor-glebas-wkt')
