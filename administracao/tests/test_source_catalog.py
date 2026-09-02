from django.test import SimpleTestCase
from django.urls import reverse

from administracao.source_catalog import (
    AREA_LABELS,
    SOURCE_PROFILES,
    get_source_profile,
    sources_for_area,
)


class SourceCatalogTests(SimpleTestCase):
    def test_item_9_areas_are_present(self):
        self.assertEqual(
            tuple(AREA_LABELS),
            ('identificacao', 'ambiental', 'fundiario', 'financeiro', 'recursos-naturais', 'agricola'),
        )

    def test_priority_one_sources_are_catalogued(self):
        for slug in ('sicar', 'prodes', 'sigef', 'sncr', 'sicor'):
            source = get_source_profile(slug)
            self.assertIsNotNone(source)
            self.assertEqual(source.priority, 1)

    def test_sicar_has_all_confirmed_layers(self):
        source = get_source_profile('sicar')
        codes = {item.code for item in source.items}
        self.assertEqual(
            codes,
            {
                'APPS', 'AREA_CONSOLIDADA', 'AREA_IMOVEL', 'AREA_POUSIO', 'HIDROGRAFIA',
                'RESERVA_LEGAL', 'SERVIDAO_ADMINISTRATIVA', 'USO_RESTRITO', 'VEGETACAO_NATIVA',
            },
        )

    def test_sicor_has_confirmed_file_names(self):
        source = get_source_profile('sicor')
        codes = [item.code for item in source.items]
        self.assertEqual(
            codes,
            [
                'SICOR_OPERACAO_BASICA',
                'SICOR_COMPLEMENTO_OPERACAO_BASICA',
                'SICOR_GLEBAS_CONTRAT',
                'SICOR_GLEBAS_WKT',
                'SICOR_PROPRIEDADES',
                'SICOR_MUTUARIOS',
            ],
        )

    def test_manual_sources_are_importable_without_inventing_operational_mapping(self):
        for slug in ('sigef', 'sncr'):
            source = get_source_profile(slug)
            self.assertIsNotNone(source)
            self.assertTrue(source.is_importable)
            self.assertEqual(source.implementation, 'RAW_FLEXIVEL')


    def test_funai_is_operational_after_real_file_validation(self):
        source = get_source_profile('funai')
        self.assertIsNotNone(source)
        self.assertTrue(source.is_importable)
        self.assertEqual(source.implementation, 'OPERACIONAL')

    def test_removed_sources_are_not_in_catalog(self):
        self.assertIsNone(get_source_profile('ana'))
        for slug in ('ana', 'focos-calor', 'florestas-publicas', 'deter', 'anm', 'zarc', 'mapbiomas'):
            self.assertIsNone(get_source_profile(slug))

    def test_every_source_belongs_to_known_area(self):
        for source in SOURCE_PROFILES:
            self.assertTrue(source.areas)
            for area in source.areas:
                self.assertIn(area, AREA_LABELS)
                self.assertIn(source, sources_for_area(area))

    def test_catalog_routes_reverse(self):
        self.assertEqual(reverse('administracao:catalogo_bases'), '/painel/bases/')
        self.assertEqual(reverse('administracao:base_detalhe', args=['sicar']), '/painel/bases/sicar/')
        self.assertEqual(
            reverse('administracao:limpar_dataset', args=['prodes', 'prodes-caatinga-supressao']),
            '/painel/bases/prodes/prodes-caatinga-supressao/limpar/',
        )
