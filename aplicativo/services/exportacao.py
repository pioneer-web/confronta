from xml.etree import ElementTree as ET

from django.db import DatabaseError

from aplicativo.repositories import (
    CamadaExportacaoInvalida,
    CamadaIndisponivel,
    ExportacaoMuitoGrande,
    ImovelDuplicado,
    ImovelNaoEncontrado,
    RepositorioTerritorial,
)
from aplicativo.validators import CarInvalido, normalizar_car


KML_NS = 'http://www.opengis.net/kml/2.2'
ET.register_namespace('', KML_NS)


class ExportacaoErro(Exception):
    pass


def _tag(nome):
    return f'{{{KML_NS}}}{nome}'


def _coord_text(coordenada):
    if len(coordenada) >= 3:
        return f'{coordenada[0]},{coordenada[1]},{coordenada[2]}'
    return f'{coordenada[0]},{coordenada[1]},0'


def _linha_coordenadas(coordenadas):
    return ' '.join(_coord_text(item) for item in coordenadas)


def _adicionar_geometria(parent, geometry):
    if not geometry:
        return
    tipo = geometry.get('type')
    coords = geometry.get('coordinates')

    if tipo == 'Point':
        point = ET.SubElement(parent, _tag('Point'))
        ET.SubElement(point, _tag('coordinates')).text = _coord_text(coords)
        return

    if tipo == 'LineString':
        line = ET.SubElement(parent, _tag('LineString'))
        ET.SubElement(line, _tag('tessellate')).text = '1'
        ET.SubElement(line, _tag('coordinates')).text = _linha_coordenadas(coords)
        return

    if tipo == 'Polygon':
        polygon = ET.SubElement(parent, _tag('Polygon'))
        ET.SubElement(polygon, _tag('tessellate')).text = '1'
        for indice, ring in enumerate(coords or []):
            boundary_name = 'outerBoundaryIs' if indice == 0 else 'innerBoundaryIs'
            boundary = ET.SubElement(polygon, _tag(boundary_name))
            linear = ET.SubElement(boundary, _tag('LinearRing'))
            ET.SubElement(linear, _tag('coordinates')).text = _linha_coordenadas(ring)
        return

    multi_map = {
        'MultiPoint': 'Point',
        'MultiLineString': 'LineString',
        'MultiPolygon': 'Polygon',
    }
    if tipo in multi_map:
        multi = ET.SubElement(parent, _tag('MultiGeometry'))
        for parte in coords or []:
            _adicionar_geometria(multi, {'type': multi_map[tipo], 'coordinates': parte})
        return

    if tipo == 'GeometryCollection':
        multi = ET.SubElement(parent, _tag('MultiGeometry'))
        for parte in geometry.get('geometries') or []:
            _adicionar_geometria(multi, parte)


def _kml_documento(nome_documento, features):
    kml = ET.Element(_tag('kml'))
    document = ET.SubElement(kml, _tag('Document'))
    ET.SubElement(document, _tag('name')).text = nome_documento

    for indice, feature in enumerate(features, start=1):
        placemark = ET.SubElement(document, _tag('Placemark'))
        props = feature.get('properties') or {}
        nome = props.get('nome') or props.get('car') or f'Feição {indice}'
        ET.SubElement(placemark, _tag('name')).text = str(nome)

        if props:
            extended = ET.SubElement(placemark, _tag('ExtendedData'))
            for chave, valor in props.items():
                if valor is None:
                    continue
                data = ET.SubElement(extended, _tag('Data'), {'name': str(chave)})
                ET.SubElement(data, _tag('value')).text = str(valor)

        _adicionar_geometria(placemark, feature.get('geometry'))

    return ET.tostring(kml, encoding='utf-8', xml_declaration=True)


class ExportacaoKmlService:
    def __init__(self, repositorio=None):
        self.repositorio = repositorio or RepositorioTerritorial()

    def exportar_imovel(self, car):
        try:
            car_normalizado = normalizar_car(car)
            imovel = self.repositorio.buscar_imovel_por_car(car_normalizado)
        except (CarInvalido, CamadaIndisponivel, ImovelNaoEncontrado, ImovelDuplicado) as exc:
            raise ExportacaoErro(str(exc)) from exc
        except DatabaseError as exc:
            raise ExportacaoErro('Não foi possível exportar o perímetro neste momento.') from exc

        feature = {
            'type': 'Feature',
            'properties': {
                'nome': f"CAR {imovel['cod_imovel']}",
                'car': imovel['cod_imovel'],
                'municipio': imovel['municipio'],
                'uf': imovel['uf'],
                'area_total_ha': imovel['area_total_ha'],
            },
            'geometry': imovel['geometry'],
        }
        return _kml_documento(f"CAR {imovel['cod_imovel']}", [feature]), imovel['cod_imovel']

    def exportar_camada(self, car, chave):
        try:
            car_normalizado = normalizar_car(car)
            camada = self.repositorio.buscar_camada_para_exportacao(car_normalizado, chave)
        except (
            CarInvalido,
            CamadaExportacaoInvalida,
            CamadaIndisponivel,
            ExportacaoMuitoGrande,
        ) as exc:
            raise ExportacaoErro(str(exc)) from exc
        except DatabaseError as exc:
            raise ExportacaoErro('Não foi possível exportar a camada neste momento.') from exc

        features = []
        for indice, feature in enumerate(camada['features'], start=1):
            props = dict(feature.get('properties') or {})
            props['nome'] = f"{camada['label']} {indice}"
            props['car'] = car_normalizado
            features.append({
                'type': 'Feature',
                'properties': props,
                'geometry': feature.get('geometry'),
            })

        return _kml_documento(
            f"{camada['label']} — {car_normalizado}",
            features,
        ), camada['label'], car_normalizado
