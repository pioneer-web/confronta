from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from administracao.services.sicar_sources import dataset_is_current_today


class SicarSameDayGuardTests(SimpleTestCase):
    @patch('administracao.services.sicar_sources.dataset_last_validated_at')
    def test_camadas_validadas_hoje_nao_precisam_novo_download(self, last_validated, _has_rows):
        # sicar_partition_has_rows é importado tardiamente; patchamos a função no módulo de tracking abaixo.
        last_validated.return_value = timezone.now()
        with patch('administracao.services.sicar_tracking.sicar_partition_has_rows', return_value=True):
            self.assertTrue(dataset_is_current_today('PE', 'sicar-perimetros'))

    @patch('administracao.services.sicar_sources.dataset_last_validated_at')
    def test_data_anterior_nao_bloqueia_nova_verificacao(self, last_validated):
        last_validated.return_value = timezone.now() - timedelta(days=1)
        with patch('administracao.services.sicar_tracking.sicar_partition_has_rows', return_value=True):
            self.assertFalse(dataset_is_current_today('PE', 'sicar-perimetros'))

    @patch('administracao.services.sicar_sources.dataset_last_validated_at')
    def test_sem_particao_no_banco_nao_pula_download(self, last_validated):
        last_validated.return_value = timezone.now()
        with patch('administracao.services.sicar_tracking.sicar_partition_has_rows', return_value=False):
            self.assertFalse(dataset_is_current_today('PE', 'sicar-perimetros'))
