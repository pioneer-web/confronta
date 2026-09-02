from django.test import SimpleTestCase

from administracao.datasets import get_dataset


class IncraAutomationSpecsTests(SimpleTestCase):
    def test_sigef_uses_official_partition_key(self):
        spec = get_dataset('incra-sigef-parcelas')
        self.assertEqual(spec.mode, 'replace_partition')
        self.assertEqual(spec.unique_key, ('parcela_co',))
        self.assertTrue(any(f.canonical == 'parcela_co' and f.required for f in spec.fields))

    def test_snci_uses_official_certificate_key(self):
        spec = get_dataset('incra-snci-certificados')
        self.assertEqual(spec.mode, 'replace_partition')
        self.assertEqual(spec.unique_key, ('num_certif',))
        self.assertTrue(any(f.canonical == 'num_certif' and f.required for f in spec.fields))

    def test_ibama_uses_seq_tad(self):
        spec = get_dataset('ibama-termos-embargo')
        self.assertEqual(spec.unique_key, ('seq_tad',))
        self.assertTrue(any(f.canonical == 'seq_tad' and f.required for f in spec.fields))
