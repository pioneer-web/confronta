import gzip
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from administracao.datasets import get_dataset
from administracao.forms import UploadBaseForm
from administracao.models import Importacao, User
from administracao.services.batch_creation import create_batch_from_uploads
from administracao.services.batch_upload import allowed_input_extensions
from administracao.services.sicor_operations import (
    INVALID_OPERATIONS_HEADER,
    IMPORTER_NOT_CONFIGURED,
    _OPERATIONS_HEADER,
    operations_reference_year,
    validate_operations_header,
)
from administracao.source_catalog import get_source_profile


@override_settings(MIN_FREE_DISK_BYTES=0, ROOT_URLCONF='administracao.tests.urls')
class SicorOperationsManageTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.batch_root = root / 'batches'
        self.recovery_root = root / 'recovery'
        self.inbox_root = root / 'inbox'
        for directory in (self.batch_root, self.recovery_root, self.inbox_root):
            directory.mkdir(parents=True)
        settings_override = override_settings(
            BATCH_STORAGE_DIR=self.batch_root,
            BATCH_DIR=self.batch_root / 'working',
            BATCH_RECOVERY_DIR=self.recovery_root,
            IMPORT_INBOX_DIR=self.inbox_root,
        )
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.admin = User.objects.create_user(
            email='sicor-ops-admin@example.test',
            password='Safe-Test-Password-123!',
            role=User.Role.ADMIN_TOTAL,
        )
        self.client.force_login(self.admin)

    @staticmethod
    def content(filename, header=None):
        text = ';'.join(header or _OPERATIONS_HEADER) + '\r\n'
        raw = text.encode('utf-8')
        return gzip.compress(raw) if filename.lower().endswith('.gz') else raw

    def test_operacoes_sicor_aparece_com_dataset_independente(self):
        profile = get_source_profile('sicor-operacoes')
        dataset = get_dataset('sicor-operacoes')
        response = self.client.get(reverse('administracao:fonte_datasets', args=['sicor_operacoes']))

        self.assertIsNotNone(profile)
        self.assertEqual(profile.label, 'Operações SICOR')
        self.assertEqual(profile.import_source_slug, 'sicor_operacoes')
        self.assertEqual(dataset.fonte_slug, 'sicor_operacoes')
        self.assertEqual(dataset.stable_table, 'sicor_operacoes')
        self.assertContains(response, 'Operações SICOR')
        self.assertContains(response, 'sicor-operacoes')

    def test_nao_interfere_com_dataset_atual_de_glebas_sicor(self):
        glebas = get_dataset('sicor-glebas-wkt')
        operacoes = get_dataset('sicor-operacoes')

        self.assertEqual(glebas.fonte_slug, 'sicor')
        self.assertEqual(glebas.data_kind, 'sicor_wkt')
        self.assertEqual(operacoes.fonte_slug, 'sicor_operacoes')
        self.assertNotEqual(glebas.stable_table, operacoes.stable_table)

    def test_arquivo_gz_oficial_e_ano_2026_sao_reconhecidos(self):
        filename = 'SICOR_OPERACAO_BASICA_ESTADO_2026.gz'
        self.assertEqual(operations_reference_year(filename), 2026)

        form = UploadBaseForm(
            files={'arquivo': SimpleUploadedFile(filename, self.content(filename))},
            source_slug='sicor_operacoes',
            dataset_slug='sicor-operacoes',
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn('accept', form.fields['arquivo'].widget.attrs)

    def test_csv_correspondente_tambem_e_aceito_e_ano_fora_do_intervalo_nao(self):
        csv_form = UploadBaseForm(
            files={'arquivo': SimpleUploadedFile(
                'SICOR_OPERACAO_BASICA_ESTADO_2025.csv',
                self.content('SICOR_OPERACAO_BASICA_ESTADO_2025.csv'),
            )},
            source_slug='sicor_operacoes',
            dataset_slug='sicor-operacoes',
        )
        self.assertTrue(csv_form.is_valid(), csv_form.errors)
        self.assertIsNone(operations_reference_year('SICOR_OPERACAO_BASICA_ESTADO_2027.gz'))

    def test_aceita_nome_sem_extensao_em_2013_e_2026(self):
        for year in (2013, 2026):
            filename = f'SICOR_OPERACAO_BASICA_ESTADO_{year}'
            form = UploadBaseForm(
                files={'arquivo': SimpleUploadedFile(filename, self.content(filename))},
                source_slug='sicor_operacoes',
                dataset_slug='sicor-operacoes',
            )
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(operations_reference_year(filename), year)

    def test_rejeita_nomes_sem_extensao_aleatorios_e_anos_fora_da_faixa(self):
        for filename in (
            'arquivo_qualquer',
            'SICOR_OPERACAO_BASICA_ESTADO_2012',
            'SICOR_OPERACAO_BASICA_ESTADO_2027',
        ):
            form = UploadBaseForm(
                files={'arquivo': SimpleUploadedFile(filename, self.content(filename))},
                source_slug='sicor_operacoes',
                dataset_slug='sicor-operacoes',
            )
            self.assertFalse(form.is_valid(), filename)

    def test_valida_cabecalho_com_47_colunas_e_primeira_coluna_literal(self):
        filename = 'SICOR_OPERACAO_BASICA_ESTADO_2026'
        self.assertEqual(len(_OPERATIONS_HEADER), 47)
        self.assertEqual(_OPERATIONS_HEADER[0], '#REF_BACEN')
        uploaded = SimpleUploadedFile(filename, self.content(filename))
        self.assertEqual(
            validate_operations_header(uploaded, filename),
            2026,
        )

    def test_cabecalho_invalido_e_rejeitado_com_mensagem_amigavel(self):
        filename = 'SICOR_OPERACAO_BASICA_ESTADO_2026'
        invalid = SimpleUploadedFile(filename, self.content(filename, ('REF_BACEN', 'NU_ORDEM')))
        form = UploadBaseForm(
            files={'arquivo': invalid},
            source_slug='sicor_operacoes',
            dataset_slug='sicor-operacoes',
        )
        self.assertFalse(form.is_valid())
        self.assertIn(INVALID_OPERATIONS_HEADER, str(form.errors))

    def test_arquivo_sem_extensao_valido_pode_criar_lote_sem_alterar_nome(self):
        filename = 'SICOR_OPERACAO_BASICA_ESTADO_2026'
        lote = create_batch_from_uploads(
            [SimpleUploadedFile(filename, self.content(filename))],
            'sicor_operacoes',
            self.admin,
        )
        self.assertEqual(lote.itens.count(), 1)
        item = lote.itens.get()
        self.assertEqual(item.nome_arquivo, filename)
        self.assertEqual(item.dataset_slug, 'sicor-operacoes')
        stored = Path(lote.extracted_path) / item.caminho_relativo
        self.assertTrue(stored.is_file())
        self.assertEqual(stored.name, filename)

    def test_demais_fontes_nao_herdam_extensao_vazia(self):
        self.assertNotIn('', allowed_input_extensions('sicor'))
        self.assertNotIn('', allowed_input_extensions('sicar'))

    def test_processamento_placeholder_falha_controladamente_sem_resposta_500(self):
        response = self.client.post(
            reverse('administracao:importar_dataset', args=['sicor_operacoes', 'sicor-operacoes']),
            {
                'arquivo': SimpleUploadedFile(
                    'SICOR_OPERACAO_BASICA_ESTADO_2026.gz',
                    self.content('SICOR_OPERACAO_BASICA_ESTADO_2026.gz'),
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        imp = Importacao.objects.get(dataset_slug='sicor-operacoes')
        self.assertEqual(imp.status, Importacao.Status.FALHOU)
        self.assertEqual(imp.motivo_rejeicao, IMPORTER_NOT_CONFIGURED)
        self.assertEqual(imp.resultado['ano_referencia'], 2026)
        self.assertEqual(imp.resultado['destino_previsto'], 'PostgreSQL do CONFRONTA')

    def test_lote_sequencial_pode_ser_iniciado_para_operacoes(self):
        response = self.client.post(
            reverse('administracao:iniciar_lote_sequencial'),
            {
                'fonte': 'sicor_operacoes',
                'total_arquivos': '1',
                'nomes_arquivos': '["SICOR_OPERACAO_BASICA_ESTADO_2026.gz"]',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
