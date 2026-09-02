import json, zipfile
from pathlib import Path
from django.core.management.base import BaseCommand


def feature_collection(name, props):
    return {'type':'FeatureCollection','name':name,'features':[{'type':'Feature','properties':props,'geometry':{'type':'Polygon','coordinates':[[[-35,-8],[-34.99,-8],[-34.99,-7.99],[-35,-7.99],[-35,-8]]]}}]}

class Command(BaseCommand):
    help='Gera ZIPs GeoJSON simples para validar identidade e segurança sem usar dados oficiais.'
    def handle(self,*args,**kwargs):
        out=Path('var/testes'); out.mkdir(parents=True,exist_ok=True)
        samples={
            'sicar_reserva_legal_teste.zip':('reserva_legal.geojson',feature_collection('reserva_legal',{'COD_IMOVEL':'PE-TESTE-001','NUM_AREA':10.2,'SITUACAO':'TESTE','TIPO':'PROPOSTA'})),
            'prodes_cerrado_teste.zip':('prodes_cerrado.geojson',feature_collection('prodes_cerrado',{'year':2024,'class':'supressao'})),
        }
        for zname,(fname,payload) in samples.items():
            with zipfile.ZipFile(out/zname,'w',zipfile.ZIP_DEFLATED) as zf: zf.writestr(fname,json.dumps(payload))
        with zipfile.ZipFile(out/'arquivo_perigoso_teste.zip','w') as zf: zf.writestr('executavel.exe',b'MZ'+b'0'*100)
        self.stdout.write(self.style.SUCCESS(f'Arquivos criados em {out.resolve()}'))
