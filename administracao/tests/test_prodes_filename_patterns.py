from django.test import SimpleTestCase

from administracao.datasets import get_dataset
from administracao.services.batch import _filename_pattern_hits


class ProdesFilenamePatternTests(SimpleTestCase):
    def test_amazonia_legal_maps_to_deforestation_profile_pattern(self):
        spec = get_dataset('prodes-amazonia-desmatamento')
        self.assertTrue(_filename_pattern_hits('yearly_deforestation_amazonia_legal_v20260717.zip', spec))

    def test_non_forest_amazonia_has_distinct_pattern(self):
        spec = get_dataset('prodes-amazonia-nao-florestal')
        self.assertTrue(_filename_pattern_hits('yearly_deforestation_nf_biome_amazonia_v20260717.zip', spec))

    def test_other_biomes_have_explicit_patterns(self):
        samples = {
            'prodes-cerrado-supressao': 'yearly_deforestation_biome_cerrado_v20260717.zip',
            'prodes-mata-atlantica-supressao': 'yearly_deforestation_biome_mata_atlantica_v20260806.zip',
            'prodes-caatinga-supressao': 'yearly_deforestation_biome_caatinga_v20260805.zip',
            'prodes-pampa-supressao': 'yearly_deforestation_biome_pampa_v20260807.zip',
        }
        for slug, filename in samples.items():
            with self.subTest(slug=slug):
                self.assertTrue(_filename_pattern_hits(filename, get_dataset(slug)))
