from django.test import SimpleTestCase

from aplicativo.validators import CarInvalido, CpfInvalido, normalizar_car, validar_cpf


class CarValidatorTests(SimpleTestCase):
    CAR = 'PE-2614105-9C74D4EF908C4BF4A177617BDC9C3D86'

    def test_normaliza_car_sem_hifens(self):
        self.assertEqual(
            normalizar_car('PE26141059C74D4EF908C4BF4A177617BDC9C3D86'),
            self.CAR,
        )

    def test_normaliza_car_com_pontos_e_espacos(self):
        self.assertEqual(
            normalizar_car('pe.2614105.9c74d4ef908c4bf4a177617bdc9c3d86 '),
            self.CAR,
        )

    def test_rejeita_car_incompleto(self):
        with self.assertRaises(CarInvalido):
            normalizar_car('PE-2614105-123')


class CpfValidatorTests(SimpleTestCase):
    def test_cpf_valido_e_normalizado(self):
        self.assertEqual(validar_cpf('529.982.247-25'), '52998224725')

    def test_cpf_invalido(self):
        with self.assertRaises(CpfInvalido):
            validar_cpf('111.111.111-11')
