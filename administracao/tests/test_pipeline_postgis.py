import io,json,zipfile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase,override_settings
from administracao.models import CamadaImportada,Importacao,User
from administracao.services.pipeline import process_import
from administracao.services.postgis import table_exists

def geojson(name,props):
    return {'type':'FeatureCollection','name':name,'features':[{'type':'Feature','properties':props,'geometry':{'type':'Polygon','coordinates':[[[-35,-8],[-34.99,-8],[-34.99,-7.99],[-35,-7.99],[-35,-8]]]}}]}
def make_zip(filename,payload):
    stream=io.BytesIO();
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as zf: zf.writestr(filename,json.dumps(payload))
    return stream.getvalue()
@override_settings(ANTIVIRUS_ENABLED=False,REQUIRE_ANTIVIRUS=False,MAX_UPLOAD_SIZE_BYTES=0,MAX_ZIP_UNCOMPRESSED_BYTES=0,MAX_ZIP_ENTRIES=100,MAX_ZIP_EXPANSION_RATIO=200,STRICT_GEOMETRY_VALIDATION=True,AUTO_REPAIR_INVALID_GEOMETRIES=True)
class PipelinePostGISTests(TransactionTestCase):
    reset_sequences=True
    def setUp(self): self.user=User.objects.create_user(email='junior.pipeline@test.local',password='SenhaForte123!',role=User.Role.ADMIN_JUNIOR)
    def test_importa_dataset_sem_afetar_outros_datasets(self):
        up=SimpleUploadedFile('reserva_legal.zip',make_zip('reserva_legal.geojson',geojson('reserva_legal',{'COD_IMOVEL':'PE-X','NUM_AREA':4.5,'SITUACAO':'TESTE','TIPO':'RL'})),content_type='application/zip')
        imp=process_import(up,'sicar-reserva-legal',self.user,context={}); self.assertEqual(imp.status,Importacao.Status.CONCLUIDO,imp.motivo_rejeicao); self.assertTrue(table_exists('dados_sicar','raw_sicar_reserva_legal')); self.assertTrue(table_exists('dados_sicar','sicar_reserva_legal')); self.assertEqual(CamadaImportada.objects.filter(dataset_slug='sicar-reserva-legal').count(),1)
    def test_bloqueia_dataset_errado(self):
        up=SimpleUploadedFile('prodes_cerrado.zip',make_zip('prodes_cerrado.geojson',geojson('prodes_cerrado',{'year':2024,'class':'supressao'})),content_type='application/zip')
        imp=process_import(up,'sicar-reserva-legal',self.user,context={}); self.assertEqual(imp.status,Importacao.Status.REJEITADO_IDENTIDADE); self.assertFalse(table_exists('dados_sicar','sicar_reserva_legal'))


    def test_duas_raws_sicar_nao_colidem_incoming_pkey(self):
        consolidada = SimpleUploadedFile(
            'AREA_CONSOLIDADA.zip',
            make_zip('AREA_CONSOLIDADA.geojson', geojson('AREA_CONSOLIDADA', {'COD_IMOVEL':'PE-C','NUM_AREA':2.5})),
            content_type='application/zip',
        )
        pousio = SimpleUploadedFile(
            'AREA_POUSIO.zip',
            make_zip('AREA_POUSIO.geojson', geojson('AREA_POUSIO', {'COD_IMOVEL':'PE-P','NUM_AREA':1.5})),
            content_type='application/zip',
        )
        imp1 = process_import(consolidada, 'sicar-area-consolidada', self.user, context={})
        imp2 = process_import(pousio, 'sicar-area-pousio', self.user, context={})
        self.assertEqual(imp1.status, Importacao.Status.CONCLUIDO, imp1.motivo_rejeicao)
        self.assertEqual(imp2.status, Importacao.Status.CONCLUIDO, imp2.motivo_rejeicao)
        self.assertTrue(table_exists('dados_sicar', 'raw_sicar_area_consolidada'))
        self.assertTrue(table_exists('dados_sicar', 'raw_sicar_area_pousio'))

    def test_geometria_invalida_e_preservada_raw_e_reparada_operacional(self):
        payload = {
            'type':'FeatureCollection',
            'name':'AREA_CONSOLIDADA',
            'features':[{
                'type':'Feature',
                'properties':{'COD_IMOVEL':'PE-BOW','NUM_AREA':1.0},
                'geometry':{
                    'type':'Polygon',
                    'coordinates':[[[-35.0,-8.0],[-34.9,-7.9],[-35.0,-7.9],[-34.9,-8.0],[-35.0,-8.0]]],
                },
            }],
        }
        up = SimpleUploadedFile(
            'AREA_CONSOLIDADA_INVALIDA.zip',
            make_zip('AREA_CONSOLIDADA.geojson', payload),
            content_type='application/zip',
        )
        imp = process_import(up, 'sicar-area-consolidada', self.user, context={})
        self.assertEqual(imp.status, Importacao.Status.CONCLUIDO, imp.motivo_rejeicao)
        self.assertGreaterEqual(imp.resultado['totais']['geometrias_invalidas_detectadas'], 1)
        self.assertGreaterEqual(imp.resultado['totais']['geometrias_corrigidas_operacional'], 1)
        self.assertEqual(imp.resultado['totais']['geometrias_nao_reparaveis'], 0)
        self.assertTrue(imp.resultado['reparo_geometrias']['raw_preservada'])

    @override_settings(AUTO_REPAIR_INVALID_GEOMETRIES=False, STRICT_GEOMETRY_VALIDATION=True)
    def test_geometria_reparavel_nao_depende_da_flag_legada_auto_repair(self):
        payload = {
            'type':'FeatureCollection',
            'name':'AREA_CONSOLIDADA',
            'features':[{
                'type':'Feature',
                'properties':{'COD_IMOVEL':'PE-LEGACY-FLAG','NUM_AREA':1.0},
                'geometry':{
                    'type':'Polygon',
                    'coordinates':[[[-35.0,-8.0],[-34.9,-7.9],[-35.0,-7.9],[-34.9,-8.0],[-35.0,-8.0]]],
                },
            }],
        }
        up = SimpleUploadedFile(
            'AREA_CONSOLIDADA_INVALIDA_FLAG.zip',
            make_zip('AREA_CONSOLIDADA.geojson', payload),
            content_type='application/zip',
        )
        imp = process_import(up, 'sicar-area-consolidada', self.user, context={})
        self.assertEqual(imp.status, Importacao.Status.CONCLUIDO, imp.motivo_rejeicao)
        self.assertGreaterEqual(imp.resultado['totais']['geometrias_corrigidas_operacional'], 1)

    def test_sicar_consolida_por_car_sem_particao_de_uf(self):
        primeiro = SimpleUploadedFile(
            'AREA_CONSOLIDADA.zip',
            make_zip('AREA_CONSOLIDADA.geojson', geojson('AREA_CONSOLIDADA', {'COD_IMOVEL':'PA-X','NUM_AREA':2.0})),
            content_type='application/zip',
        )
        segundo = SimpleUploadedFile(
            'AREA_CONSOLIDADA.zip',
            make_zip('AREA_CONSOLIDADA.geojson', geojson('AREA_CONSOLIDADA', {'COD_IMOVEL':'MT-X','NUM_AREA':3.0})),
            content_type='application/zip',
        )
        imp_pa = process_import(primeiro, 'sicar-area-consolidada', self.user, context={})
        imp_mt = process_import(segundo, 'sicar-area-consolidada', self.user, context={})
        self.assertEqual(imp_pa.status, Importacao.Status.CONCLUIDO, imp_pa.motivo_rejeicao)
        self.assertEqual(imp_mt.status, Importacao.Status.CONCLUIDO, imp_mt.motivo_rejeicao)
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT cod_imovel FROM dados_sicar.sicar_area_consolidada ORDER BY cod_imovel")
            self.assertEqual(cursor.fetchall(), [('MT-X',), ('PA-X',)])
        self.assertTrue(table_exists('dados_sicar', 'raw_sicar_area_consolidada'))


    def test_sicar_lote_estadual_substitui_somente_a_uf_confirmada(self):
        from django.db import connection

        pa_v1 = SimpleUploadedFile(
            'AREA_CONSOLIDADA_PA.zip',
            make_zip('AREA_CONSOLIDADA.geojson', geojson('AREA_CONSOLIDADA', {'COD_IMOVEL':'PA-ANTIGO','NUM_AREA':2.0})),
            content_type='application/zip',
        )
        mt_v1 = SimpleUploadedFile(
            'AREA_CONSOLIDADA_MT.zip',
            make_zip('AREA_CONSOLIDADA.geojson', geojson('AREA_CONSOLIDADA', {'COD_IMOVEL':'MT-MANTER','NUM_AREA':3.0})),
            content_type='application/zip',
        )
        pa_v2 = SimpleUploadedFile(
            'AREA_CONSOLIDADA_PA_NOVA.zip',
            make_zip('AREA_CONSOLIDADA.geojson', geojson('AREA_CONSOLIDADA', {'COD_IMOVEL':'PA-NOVO','NUM_AREA':4.0})),
            content_type='application/zip',
        )

        imp_pa1 = process_import(pa_v1, 'sicar-area-consolidada', self.user, context={'uf':'PA','force_validate_uf':True})
        imp_mt = process_import(mt_v1, 'sicar-area-consolidada', self.user, context={'uf':'MT','force_validate_uf':True})
        imp_pa2 = process_import(pa_v2, 'sicar-area-consolidada', self.user, context={'uf':'PA','force_validate_uf':True})

        self.assertEqual(imp_pa1.status, Importacao.Status.CONCLUIDO, imp_pa1.motivo_rejeicao)
        self.assertEqual(imp_mt.status, Importacao.Status.CONCLUIDO, imp_mt.motivo_rejeicao)
        self.assertEqual(imp_pa2.status, Importacao.Status.CONCLUIDO, imp_pa2.motivo_rejeicao)
        with connection.cursor() as cursor:
            cursor.execute("SELECT cod_imovel FROM dados_sicar.sicar_area_consolidada ORDER BY cod_imovel")
            self.assertEqual(cursor.fetchall(), [('MT-MANTER',), ('PA-NOVO',)])
        self.assertGreaterEqual(imp_pa2.resultado['promocao']['normalizacao']['registros_substituidos_uf'], 1)

    def test_sicar_lote_estadual_aceita_ufs_vizinhas_quando_uf_administrativa_esta_presente(self):
        payload = {
            'type':'FeatureCollection', 'name':'AREA_IMOVEL',
            'features':[
                {'type':'Feature','properties':{'COD_IMOVEL':'PE-A','NUM_AREA':1.0},'geometry':{'type':'Polygon','coordinates':[[[-35,-8],[-34.99,-8],[-34.99,-7.99],[-35,-7.99],[-35,-8]]]}},
                {'type':'Feature','properties':{'COD_IMOVEL':'AL-B','NUM_AREA':2.0},'geometry':{'type':'Polygon','coordinates':[[[-36,-9],[-35.99,-9],[-35.99,-8.99],[-36,-8.99],[-36,-9]]]}},
            ]
        }
        up = SimpleUploadedFile('AREA_IMOVEL_MISTO.zip', make_zip('AREA_IMOVEL.geojson', payload), content_type='application/zip')
        imp = process_import(
            up, 'sicar-perimetros', self.user,
            context={'uf':'PE','force_validate_uf':True},
        )
        self.assertEqual(imp.status, Importacao.Status.CONCLUIDO, imp.motivo_rejeicao)
        self.assertEqual(imp.resultado['ufs_sicar_detectadas']['uf_administrativa'], 'PE')
        self.assertEqual(imp.resultado['ufs_sicar_detectadas']['ufs_adicionais_aceitas'], ['AL'])
        with connection.cursor() as cursor:
            cursor.execute("SELECT cod_imovel FROM dados_sicar.sicar_imoveis ORDER BY cod_imovel")
            self.assertEqual(cursor.fetchall(), [('AL-B',), ('PE-A',)])

    def test_sicar_lote_estadual_bloqueia_quando_uf_administrativa_nao_esta_no_conteudo(self):
        payload = {
            'type':'FeatureCollection', 'name':'AREA_IMOVEL',
            'features':[
                {'type':'Feature','properties':{'COD_IMOVEL':'AL-A','NUM_AREA':1.0},'geometry':{'type':'Polygon','coordinates':[[[-36,-9],[-35.99,-9],[-35.99,-8.99],[-36,-8.99],[-36,-9]]]}},
                {'type':'Feature','properties':{'COD_IMOVEL':'PB-B','NUM_AREA':2.0},'geometry':{'type':'Polygon','coordinates':[[[-37,-7],[-36.99,-7],[-36.99,-6.99],[-37,-6.99],[-37,-7]]]}},
            ]
        }
        up = SimpleUploadedFile('AREA_IMOVEL_SEM_PE.zip', make_zip('AREA_IMOVEL.geojson', payload), content_type='application/zip')
        imp = process_import(
            up, 'sicar-perimetros', self.user,
            context={'uf':'PE','force_validate_uf':True},
        )
        self.assertEqual(imp.status, Importacao.Status.FALHOU)
        self.assertIn('não foi encontrada no conteúdo', imp.motivo_rejeicao)

    def test_sicar_uf_vizinha_e_atualizada_por_car_sem_apagar_restante_do_estado(self):
        al_payload = {
            'type':'FeatureCollection', 'name':'AREA_IMOVEL',
            'features':[
                {'type':'Feature','properties':{'COD_IMOVEL':'AL-MANTER','NUM_AREA':10.0},'geometry':{'type':'Polygon','coordinates':[[[-36.5,-9.5],[-36.49,-9.5],[-36.49,-9.49],[-36.5,-9.49],[-36.5,-9.5]]]}},
                {'type':'Feature','properties':{'COD_IMOVEL':'AL-DIVISA','NUM_AREA':20.0},'geometry':{'type':'Polygon','coordinates':[[[-36,-9],[-35.99,-9],[-35.99,-8.99],[-36,-8.99],[-36,-9]]]}},
            ]
        }
        pe_payload = {
            'type':'FeatureCollection', 'name':'AREA_IMOVEL',
            'features':[
                {'type':'Feature','properties':{'COD_IMOVEL':'PE-NOVO','NUM_AREA':30.0},'geometry':{'type':'Polygon','coordinates':[[[-35,-8],[-34.99,-8],[-34.99,-7.99],[-35,-7.99],[-35,-8]]]}},
                {'type':'Feature','properties':{'COD_IMOVEL':'AL-DIVISA','NUM_AREA':99.0},'geometry':{'type':'Polygon','coordinates':[[[-36,-9],[-35.98,-9],[-35.98,-8.98],[-36,-8.98],[-36,-9]]]}},
            ]
        }
        al = SimpleUploadedFile('AREA_IMOVEL_AL.zip', make_zip('AREA_IMOVEL.geojson', al_payload), content_type='application/zip')
        pe = SimpleUploadedFile('AREA_IMOVEL_PE.zip', make_zip('AREA_IMOVEL.geojson', pe_payload), content_type='application/zip')

        imp_al = process_import(al, 'sicar-perimetros', self.user, context={'uf':'AL','force_validate_uf':True})
        imp_pe = process_import(pe, 'sicar-perimetros', self.user, context={'uf':'PE','force_validate_uf':True})
        self.assertEqual(imp_al.status, Importacao.Status.CONCLUIDO, imp_al.motivo_rejeicao)
        self.assertEqual(imp_pe.status, Importacao.Status.CONCLUIDO, imp_pe.motivo_rejeicao)

        with connection.cursor() as cursor:
            cursor.execute("SELECT cod_imovel, num_area FROM dados_sicar.sicar_imoveis ORDER BY cod_imovel")
            rows = cursor.fetchall()
        self.assertEqual([row[0] for row in rows], ['AL-DIVISA', 'AL-MANTER', 'PE-NOVO'])
        values = {row[0]: float(row[1]) for row in rows}
        self.assertEqual(values['AL-MANTER'], 10.0)
        self.assertEqual(values['AL-DIVISA'], 99.0)
        self.assertGreaterEqual(
            imp_pe.resultado['promocao']['normalizacao']['registros_substituidos_ufs_vizinhas'], 1
        )

    def test_sicar_aceita_multiplas_ufs_no_mesmo_arquivo(self):
        payload = {
            'type':'FeatureCollection', 'name':'AREA_IMOVEL',
            'features':[
                {'type':'Feature','properties':{'COD_IMOVEL':'PE-A','NUM_AREA':1.0},'geometry':{'type':'Polygon','coordinates':[[[-35,-8],[-34.99,-8],[-34.99,-7.99],[-35,-7.99],[-35,-8]]]}},
                {'type':'Feature','properties':{'COD_IMOVEL':'AL-B','NUM_AREA':2.0},'geometry':{'type':'Polygon','coordinates':[[[-36,-9],[-35.99,-9],[-35.99,-8.99],[-36,-8.99],[-36,-9]]]}},
                {'type':'Feature','properties':{'COD_IMOVEL':'PB-C','NUM_AREA':3.0},'geometry':{'type':'Polygon','coordinates':[[[-37,-7],[-36.99,-7],[-36.99,-6.99],[-37,-6.99],[-37,-7]]]}},
            ]
        }
        up = SimpleUploadedFile('AREA_IMOVEL.zip', make_zip('AREA_IMOVEL.geojson', payload), content_type='application/zip')
        imp = process_import(up, 'sicar-perimetros', self.user, context={})
        self.assertEqual(imp.status, Importacao.Status.CONCLUIDO, imp.motivo_rejeicao)
        self.assertEqual(set(imp.resultado['ufs_sicar_detectadas']['detectadas']), {'PE','AL','PB'})


    def test_sicar_lote_estadual_bloqueia_registro_sem_cod_imovel(self):
        payload = {
            'type':'FeatureCollection', 'name':'AREA_IMOVEL',
            'features':[
                {'type':'Feature','properties':{'COD_IMOVEL':'PE-A','NUM_AREA':1.0},'geometry':{'type':'Polygon','coordinates':[[[-35,-8],[-34.99,-8],[-34.99,-7.99],[-35,-7.99],[-35,-8]]]}},
                {'type':'Feature','properties':{'COD_IMOVEL':None,'NUM_AREA':2.0},'geometry':{'type':'Polygon','coordinates':[[[-35.2,-8.2],[-35.19,-8.2],[-35.19,-8.19],[-35.2,-8.19],[-35.2,-8.2]]]}},
            ]
        }
        up = SimpleUploadedFile(
            'AREA_IMOVEL_PE_COM_COD_NULO.zip',
            make_zip('AREA_IMOVEL.geojson', payload),
            content_type='application/zip',
        )
        imp = process_import(
            up, 'sicar-perimetros', self.user,
            context={'uf':'PE','force_validate_uf':True},
        )
        self.assertEqual(imp.status, Importacao.Status.FALHOU)
        self.assertIn('fora do padrão', imp.motivo_rejeicao)
