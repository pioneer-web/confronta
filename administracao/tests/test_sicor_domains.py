import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from administracao.datasets import get_dataset
from administracao.models import Importacao, SicorInstituicao, SicorPrograma
from administracao.services.sicor_domain_import import (
    _normalize_numeric_code, identify_domain_header, process_sicor_domain_import,
)


class SicorDomainTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.usuario = User.objects.create_user(
            email='teste-sicor@example.com',
            password='senha-teste',
        )

    def test_instituicoes_preservam_codigo_e_decodificam_latin1(self):
        content = '#CNPJ_IF;NOME_IF;SEGMENTO_IF\n00000208;BANCO PÚBLICO;COOPERATIVA DE CRÉDITO\n'.encode('latin-1')
        slug, headers = identify_domain_header(io.BytesIO(content), 'sicor-instituicoes')
        self.assertEqual(slug, 'sicor-instituicoes')
        self.assertEqual(headers[0], 'CNPJ_IF')

        upload = SimpleUploadedFile('instituicoes.csv', content, content_type='text/csv')
        importacao = process_sicor_domain_import(
            upload,
            get_dataset('sicor-instituicoes'),
            self.usuario,
        )

        self.assertEqual(importacao.status, Importacao.Status.CONCLUIDO)
        instituicao = SicorInstituicao.objects.get(cnpj_if='00000208')
        self.assertEqual(instituicao.nome_if, 'BANCO PÚBLICO')
        self.assertEqual(instituicao.segmento_if, 'COOPERATIVA DE CRÉDITO')

    def test_programas_normalizam_codigos_e_nao_apagam_instituicoes(self):
        self.assertEqual(_normalize_numeric_code('1', 4), '0001')
        self.assertEqual(_normalize_numeric_code('50', 4), '0050')
        self.assertEqual(_normalize_numeric_code('100', 4), '0100')

        instituicao = SicorInstituicao.objects.create(
            cnpj_if='00000208',
            nome_if='BANCO PÚBLICO',
            segmento_if='COOPERATIVA DE CRÉDITO',
        )
        content = b'#CODIGO;DESCRICAO;CAMPO_EXTRA\n1;Programa teste;ignorado\n'
        slug, headers = identify_domain_header(io.BytesIO(content), 'sicor-programas')
        self.assertEqual(slug, 'sicor-programas')
        self.assertEqual(headers, ['CODIGO', 'DESCRICAO', 'CAMPO_EXTRA'])

        upload = SimpleUploadedFile('programas.csv', content, content_type='text/csv')
        importacao = process_sicor_domain_import(
            upload,
            get_dataset('sicor-programas'),
            self.usuario,
        )

        self.assertEqual(importacao.status, Importacao.Status.CONCLUIDO)
        programa = SicorPrograma.objects.get(cd_programa='0001')
        self.assertEqual(programa.descricao, 'Programa teste')
        self.assertTrue(SicorInstituicao.objects.filter(pk=instituicao.pk).exists())
