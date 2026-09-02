from pathlib import Path
from types import SimpleNamespace
import tempfile

from django.test import SimpleTestCase

from administracao.forms import ImportacaoLoteForm, UploadBaseForm
from administracao.services.batch import (
    _detect_uf, _detect_uf_hint, _filename_token_hits, _resolve_sicar_uf, BATCH_CLASSIFIER_VERSION, calculate_batch_progress,
)
from administracao.services.partitioning import raw_table_for_import, UF_CODES, UF_NAMES
from administracao.services.sicar_tracking import fingerprint_layer_content
from administracao.datasets import get_dataset


class BatchImportUnitTests(SimpleTestCase):
    def test_detecta_uf_em_caminho_apenas_como_informacao(self):
        self.assertEqual(_detect_uf('SICAR/PA/AREA_IMOVEL.zip'), 'PA')
        self.assertEqual(_detect_uf('lote/2026/MT/APPS.zip'), 'MT')
        self.assertEqual(_detect_uf('SICAR/AREA_IMOVEL.zip'), '')

    def test_raw_sicar_nao_e_particionada_por_uf(self):
        spec = get_dataset('sicar-area-consolidada')
        self.assertEqual(raw_table_for_import(spec, {'uf':'PA'}), 'raw_sicar_area_consolidada')
        self.assertEqual(raw_table_for_import(spec, {}), 'raw_sicar_area_consolidada')

    def test_form_manual_sicar_nao_exige_uf(self):
        form = UploadBaseForm()
        self.assertNotIn('uf', form.fields)



    def test_hint_uf_funciona_em_nome_de_arquivo_sem_substring_solto(self):
        self.assertEqual(_detect_uf_hint('item_0001/PE_AREA_IMOVEL.zip'), 'PE')
        self.assertEqual(_detect_uf_hint('item_0001/AREA_IMOVEL_MT_2026.zip'), 'MT')
        self.assertEqual(_detect_uf_hint('item_0001/APPS.zip'), '')

    def test_catalogo_sicar_tem_27_ufs(self):
        self.assertEqual(len(UF_CODES), 27)
        self.assertEqual(set(UF_CODES), set(UF_NAMES))

    def test_form_lote_pode_bloquear_fonte_sicar(self):
        form = ImportacaoLoteForm(fonte_locked='sicar')
        self.assertEqual(form.fields['fonte'].initial, 'sicar')
        self.assertTrue(form.fields['fonte'].widget.is_hidden)

    def test_form_lote_sicar_permite_uf_unica_ou_deteccao_automatica(self):
        form = ImportacaoLoteForm(fonte_locked='sicar')
        self.assertIn('uf', form.fields)
        self.assertEqual(form.fields['uf'].choices[0][0], '')

    def test_form_lote_sicar_pode_bloquear_uma_uf(self):
        form = ImportacaoLoteForm(fonte_locked='sicar', uf_locked='PE')
        self.assertEqual(form.fields['uf'].initial, 'PE')
        self.assertTrue(form.fields['uf'].widget.is_hidden)

    def test_resolve_sicar_uf_aceita_vizinhas_quando_uf_administrativa_esta_presente(self):
        item = SimpleNamespace(uf='PE', caminho_relativo='item_0001/AREA_IMOVEL.zip', nome_arquivo='AREA_IMOVEL.zip')
        classification = {'sicar_uf': {'uf': '', 'detectadas': ['AL', 'PB', 'PE'], 'confiavel': False}}
        uf, error = _resolve_sicar_uf(item, classification)
        self.assertEqual(uf, 'PE')
        self.assertEqual(error, '')

    def test_resolve_sicar_uf_recusa_se_administrativa_nao_aparece_na_amostra(self):
        item = SimpleNamespace(uf='PE', caminho_relativo='item_0001/AREA_IMOVEL.zip', nome_arquivo='AREA_IMOVEL.zip')
        classification = {'sicar_uf': {'uf': '', 'detectadas': ['AL', 'PB'], 'confiavel': False}}
        uf, error = _resolve_sicar_uf(item, classification)
        self.assertEqual(uf, '')
        self.assertIn('não foi encontrada', error)

    def test_classificador_lote_sicar_prioriza_nome_oficial(self):
        casos = {
            'APPS.zip': 'sicar-app',
            'AREA_CONSOLIDADA.zip': 'sicar-area-consolidada',
            'AREA_IMOVEL.zip': 'sicar-perimetros',
            'AREA_POUSIO.zip': 'sicar-area-pousio',
            'HIDROGRAFIA.zip': 'sicar-hidrografia',
            'RESERVA_LEGAL.zip': 'sicar-reserva-legal',
            'SERVIDAO_ADMINISTRATIVA.zip': 'sicar-servidao-administrativa',
            'USO_RESTRITO.zip': 'sicar-uso-restrito',
            'VEGETACAO_NATIVA.zip': 'sicar-vegetacao-nativa',
        }
        specs = [get_dataset(slug) for slug in casos.values()]
        for arquivo, esperado in casos.items():
            hits = [spec.slug for spec in specs if _filename_token_hits(arquivo, spec)]
            self.assertIn(esperado, hits, arquivo)
            # O maior token específico deve pertencer ao dataset esperado.
            strengths = {
                spec.slug: max([len(t) for t in _filename_token_hits(arquivo, spec)] or [0])
                for spec in specs
            }
            self.assertEqual(max(strengths, key=strengths.get), esperado, arquivo)

    def test_classificador_lote_tem_versao_nova(self):
        self.assertGreaterEqual(BATCH_CLASSIFIER_VERSION, 2)

    def test_app_curto_nao_casa_com_area_consolidada(self):
        app = get_dataset('sicar-app')
        self.assertEqual(_filename_token_hits('AREA_CONSOLIDADA.zip', app), [])
        self.assertTrue(_filename_token_hits('APPS.zip', app))


    def test_classificador_lote_incra_prioriza_nomes_discriminantes(self):
        assent = get_dataset('incra-assentamentos')
        quil = get_dataset('incra-quilombolas')
        self.assertTrue(_filename_token_hits('Assentamento Brasil.zip', assent))
        self.assertFalse(_filename_token_hits('Assentamento Brasil.zip', quil))
        self.assertTrue(_filename_token_hits('Áreas de Quilombolas.zip', quil))
        self.assertFalse(_filename_token_hits('Áreas de Quilombolas.zip', assent))


    def test_classificador_lote_icmbio_distingue_uc_federal_de_embargo(self):
        ucs = get_dataset('icmbio-unidades-conservacao-federais')
        embargo = get_dataset('icmbio-areas-embargadas')
        arquivo = 'copy_of_limite_ucs_federais_082026.zip'
        self.assertTrue(_filename_token_hits(arquivo, ucs))
        self.assertFalse(_filename_token_hits(arquivo, embargo))

    def test_classificador_lote_prodes_prioriza_bioma_no_nome(self):
        cerrado = get_dataset('prodes-cerrado-supressao')
        pantanal = get_dataset('prodes-pantanal-supressao')
        arquivo = 'yearly_deforestation_biome_cerrado_v20260717.zip'
        self.assertIn('cerrado', [str(t).lower() for t in _filename_token_hits(arquivo, cerrado)])
        self.assertNotIn('pantanal', [str(t).lower() for t in _filename_token_hits(arquivo, pantanal)])

    def test_form_lote_oferece_multiplos_arquivos_e_todas_as_fontes(self):
        form = ImportacaoLoteForm()
        values = {value for value, _label in form.fields['fonte'].choices}
        self.assertEqual(values, {'sicar','ibama','icmbio','cnuc','prodes','incra'})
        self.assertIn('arquivos', form.fields)
        self.assertIn('arquivo_lote', form.fields)

    def test_fingerprint_shapefile_ignora_metadados_auxiliares_e_data_dbf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shp = root / 'AREA_IMOVEL.shp'
            dbf = root / 'AREA_IMOVEL.dbf'
            prj = root / 'AREA_IMOVEL.prj'
            cpg = root / 'AREA_IMOVEL.cpg'
            xml = root / 'AREA_IMOVEL.xml'
            qix = root / 'AREA_IMOVEL.qix'

            shp.write_bytes(b'GEOMETRIA-ESTAVEL')
            # Byte 0 = versão; bytes 1..3 = data da última escrita no DBF.
            dbf.write_bytes(bytes([3, 126, 8, 12]) + b'ATRIBUTOS-ESTAVEIS')
            prj.write_text('SIRGAS 2000', encoding='utf-8')
            cpg.write_text('UTF-8', encoding='utf-8')
            xml.write_text('metadado 1', encoding='utf-8')
            qix.write_bytes(b'indice 1')

            layer = {'dataset_path': str(shp)}
            primeiro = fingerprint_layer_content(layer)

            dbf.write_bytes(bytes([3, 126, 9, 1]) + b'ATRIBUTOS-ESTAVEIS')
            xml.write_text('metadado 2', encoding='utf-8')
            qix.write_bytes(b'indice 2')
            segundo = fingerprint_layer_content(layer)
            self.assertEqual(primeiro, segundo)

            dbf.write_bytes(bytes([3, 126, 9, 1]) + b'ATRIBUTOS-ALTERADOS')
            terceiro = fingerprint_layer_content(layer)
            self.assertNotEqual(segundo, terceiro)

    def test_progresso_fase_importacao_considera_apenas_itens_confirmados(self):
        from types import SimpleNamespace
        lote = SimpleNamespace(
            fonte='SICAR',
            resultado={'fase': 'IMPORTACAO', 'itens_confirmados': [2, 3]},
        )
        itens = [
            SimpleNamespace(id=1, progresso=100),  # sem alteração na pré-análise
            SimpleNamespace(id=2, progresso=20),
            SimpleNamespace(id=3, progresso=40),
        ]
        self.assertEqual(calculate_batch_progress(lote, itens), 30)

    def test_progresso_fase_analise_considera_todos_os_itens(self):
        from types import SimpleNamespace
        lote = SimpleNamespace(fonte='SICAR', resultado={'fase': 'ANALISE'})
        itens = [SimpleNamespace(id=1, progresso=100), SimpleNamespace(id=2, progresso=20)]
        self.assertEqual(calculate_batch_progress(lote, itens), 60)
