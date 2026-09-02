from django.test import SimpleTestCase
from administracao.datasets import get_dataset
from administracao.services.dataset_identity import validate_dataset_identity
from administracao.services.exceptions import DatasetIdentityError


class DatasetIdentityTests(SimpleTestCase):
    def layer(self, name, fields, geom='Polygon'):
        return {'dataset_name': name + '.shp', 'layer_name': name, 'fields': fields, 'geometry_type': geom}

    def test_confirma_sicar_reserva_legal(self):
        r = validate_dataset_identity(
            [self.layer('reserva_legal', ['COD_IMOVEL', 'NUM_AREA', 'SITUACAO', 'TIPO'])],
            get_dataset('sicar-reserva-legal'),
        )
        self.assertEqual(r['status'], 'CONFIRMADO')

    def test_bloqueia_prodes_em_reserva_legal(self):
        with self.assertRaises(DatasetIdentityError):
            validate_dataset_identity(
                [self.layer('prodes_cerrado', ['year', 'class'])],
                get_dataset('sicar-reserva-legal'),
            )

    def test_cnuc_rejeita_ponto(self):
        with self.assertRaises(DatasetIdentityError):
            validate_dataset_identity(
                [self.layer('cnuc_unidades', ['nome_uc', 'categoria'], geom='Point')],
                get_dataset('cnuc-unidades-conservacao'),
            )

    def test_confirma_arquivo_oficial_ibama_pelos_campos_reais(self):
        fields = [
            'objectid', 'seq_tad', 'num_tad', 'serie_tad', 'origem_geo', 'uf',
            'municipio', 'sit_desmat', 'tipo_area', 'num_auto_i', 'num_proces',
            'des_tad', 'des_infrac', 'dat_embarg', 'qtd_area_d', 'qtd_area_e',
        ]
        r = validate_dataset_identity(
            [self.layer('adm_embargo_ibama_a', fields)],
            get_dataset('ibama-termos-embargo'),
        )
        self.assertEqual(r['status'], 'CONFIRMADO')

    def test_confirma_sicar_perimetros_mesmo_se_fonte_renomear_camada(self):
        fields = [
            'COD_IMOVEL', 'NUM_AREA', 'UF', 'NOM_MUNICI', 'COD_MUNICI',
            'MOD_FISCAL', 'TIPO_IMOVE', 'SITUACAO', 'CONDICAO_I',
        ]
        r = validate_dataset_identity(
            [self.layer('camada_oficial_renomeada', fields)],
            get_dataset('sicar-perimetros'),
        )
        self.assertEqual(r['status'], 'CONFIRMADO')
        self.assertEqual(r['criterio_confirmacao'], 'ESTRUTURA_FORTE_APOS_RENOMEACAO')


    def test_sicar_perimetros_mapeia_status_tipo_e_condicao_oficiais(self):
        spec = get_dataset('sicar-perimetros')
        fields = {field.canonical: field.aliases for field in spec.fields}
        self.assertIn('IND_STATUS', fields['situacao_car'])
        self.assertIn('IND_TIPO', fields['tipo_imovel'])
        self.assertIn('DES_CONDIC', fields['condicao'])

    def test_assinatura_historica_permite_renomeacao_fisica_sem_relaxar_estrutura(self):
        layer = self.layer('novo_nome_publicado', ['COD_IMOVEL', 'NUM_AREA'])
        layer['signature'] = 'abc123'
        r = validate_dataset_identity(
            [layer],
            get_dataset('sicar-area-consolidada'),
            previous_snapshot={'signature': 'abc123'},
        )
        self.assertEqual(r['status'], 'CONFIRMADO')
        self.assertEqual(r['criterio_confirmacao'], 'ASSINATURA_HISTORICA_CONFIRMADA')

    def test_historico_aceita_precision_e_polygon_multi_mesmo_com_nome_novo(self):
        layer = self.layer('nome_novo_sem_token', ['COD_IMOVEL', 'NUM_AREA'], geom='MultiPolygon')
        layer['signature'] = 'assinatura-nova'
        previous = {
            'signature': 'assinatura-antiga',
            'geometry_type': 'Polygon',
            'fields': [
                {'name': 'COD_IMOVEL'},
                {'name': 'NUM_AREA'},
            ],
        }
        r = validate_dataset_identity(
            [layer],
            get_dataset('sicar-area-consolidada'),
            previous_snapshot=previous,
        )
        self.assertEqual(r['status'], 'CONFIRMADO')
        self.assertEqual(r['criterio_confirmacao'], 'ESTRUTURA_HISTORICA_COMPATIVEL')

    def test_sicar_generico_nao_lista_tres_falsos_competidores(self):
        from administracao.datasets import get_dataset
        spec = get_dataset('sicar-area-consolidada')
        r = validate_dataset_identity([self.layer('AREA_CONSOLIDADA_1', ['COD_IMOVEL','NUM_AREA'])], spec)
        self.assertEqual(r['status'], 'CONFIRMADO')
        self.assertEqual(r['possiveis_outros_datasets'], [])

    def test_cnuc_pacote_oficial_multicamadas_seleciona_poligono_e_ignora_ponto(self):
        pol = self.layer(
            'shp_cnuc_2024_02_pol',
            ['uc_id', 'cd_cnuc', 'nome_uc', 'cria_ano', 'grupo', 'categoria', 'esfera'],
            geom='Polygon',
        )
        pt = self.layer(
            'shp_cnuc_2024_02_pt',
            ['uc_id', 'cd_cnuc', 'nome_uc', 'cria_ano', 'grupo', 'categoria', 'esfera'],
            geom='Point',
        )
        r = validate_dataset_identity([pt, pol], get_dataset('cnuc-unidades-conservacao'))
        self.assertEqual(r['status'], 'CONFIRMADO')
        self.assertEqual(r['camada'], 'shp_cnuc_2024_02_pol')
        self.assertEqual(r['selecao_camada']['camadas_encontradas'], 2)
        self.assertEqual(len(r['selecao_camada']['camadas_auxiliares_ignoradas']), 1)

    def test_pacote_multicamadas_ambiguo_continua_bloqueado(self):
        a = self.layer('camada_a', ['nome_uc', 'categoria'], geom='Polygon')
        b = self.layer('camada_b', ['nome_uc', 'categoria'], geom='Polygon')
        with self.assertRaises(DatasetIdentityError):
            validate_dataset_identity([a, b], get_dataset('cnuc-unidades-conservacao'))

    def test_limite_ucs_federais_nao_e_aceito_como_embargo_e_sugere_dataset_icmbio_correto(self):
        layer = self.layer(
            'copy_of_limite_ucs_federais_082026',
            [
                'id', 'nomeuc', 'cnuc', 'criacaoano', 'areahaalb', 'perimm',
                'criacaoato', 'esferaadm', 'grupouc', 'biomas', 'gregional',
                'cat_IUCN', 'uf', 'categoria_', 'sigla_cate', 'dominio',
            ],
            geom='Polygon',
        )
        with self.assertRaises(DatasetIdentityError) as ctx:
            validate_dataset_identity([layer], get_dataset('icmbio-areas-embargadas'))
        self.assertEqual(ctx.exception.report['status'], 'INCOMPATIVEL')
        self.assertEqual(
            ctx.exception.report['dataset_sugerido']['slug'],
            'icmbio-unidades-conservacao-federais',
        )

    def test_confirma_unidades_conservacao_federais_icmbio_pelo_arquivo_real(self):
        layer = self.layer(
            'limite_ucs_federais_082026',
            [
                'id', 'nomeuc', 'cnuc', 'criacaoano', 'areahaalb', 'perimm',
                'criacaoato', 'esferaadm', 'grupouc', 'biomas', 'gregional',
                'fusoabrang', 'demarcacao', 'escalauc', 'bioma_pred',
                'cat_IUCN', 'uf', 'categoria_', 'sigla_cate', 'dominio',
            ],
            geom='Polygon',
        )
        result = validate_dataset_identity(
            [layer],
            get_dataset('icmbio-unidades-conservacao-federais'),
        )
        self.assertEqual(result['status'], 'CONFIRMADO')
        self.assertEqual(result['dataset'], 'icmbio-unidades-conservacao-federais')

    def test_cnuc_nao_confunde_limite_ucs_federais_icmbio_com_cnuc(self):
        layer = self.layer(
            'limite_ucs_federais_082026',
            ['id', 'nomeuc', 'cnuc', 'criacaoano', 'esferaadm', 'grupouc', 'categoria_'],
            geom='Polygon',
        )
        with self.assertRaises(DatasetIdentityError):
            validate_dataset_identity([layer], get_dataset('cnuc-unidades-conservacao'))

    def test_incra_assentamento_aceita_campos_dbf_oficiais_truncados(self):
        fields = ['cd_sipra','nome_proje','municipio','uf','area_hecta','capacidade','num_famili','fase','descricao_']
        r = validate_dataset_identity(
            [self.layer('Assentamento Brasil', fields)],
            get_dataset('incra-assentamentos'),
        )
        self.assertEqual(r['status'], 'CONFIRMADO')

    def test_incra_quilombola_aceita_campos_publicados_pelo_acervo(self):
        fields = ['cd_sr','nr_process','nm_comunid','nm_municip','cd_uf','nr_area_ha','fase']
        r = validate_dataset_identity(
            [self.layer('Áreas de Quilombolas', fields)],
            get_dataset('incra-quilombolas'),
        )
        self.assertEqual(r['status'], 'CONFIRMADO')

    def test_reserva_legal_enviada_em_vegetacao_nativa_sugere_dataset_correto(self):
        with self.assertRaises(DatasetIdentityError) as ctx:
            validate_dataset_identity(
                [self.layer('RESERVA_LEGAL_1', ['COD_IMOVEL','NUM_AREA','SITUACAO','TIPO'])],
                get_dataset('sicar-vegetacao-nativa'),
            )
        self.assertEqual(ctx.exception.report['status'], 'INCOMPATIVEL')
        self.assertEqual(ctx.exception.report['dataset_sugerido']['slug'], 'sicar-reserva-legal')
