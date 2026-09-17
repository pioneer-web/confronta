import io
import json
import zipfile
import xml.etree.ElementTree as ET

from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry


class ConsultaGeometriaErro(ValueError):
    pass


MAX_KMZ_ENTRIES = 50
MAX_KMZ_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_VERTICES = 100_000


def _upload_bytes(uploaded_file):
    limite = int(getattr(settings, 'QUERY_GEOMETRY_MAX_UPLOAD_BYTES', 5 * 1024 * 1024))
    tamanho = getattr(uploaded_file, 'size', None)
    if tamanho is not None and limite > 0 and int(tamanho) > limite:
        raise ConsultaGeometriaErro('O arquivo excede o limite de 5 MB para consulta.')

    data = uploaded_file.read(limite + 1 if limite > 0 else -1)
    if limite > 0 and len(data) > limite:
        raise ConsultaGeometriaErro('O arquivo excede o limite de 5 MB para consulta.')
    if not data:
        raise ConsultaGeometriaErro('O arquivo enviado está vazio.')
    return data


def _kml_de_kmz(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_KMZ_ENTRIES:
                raise ConsultaGeometriaErro('O KMZ possui arquivos internos demais para uma consulta.')
            if sum(max(0, item.file_size) for item in infos) > MAX_KMZ_UNCOMPRESSED_BYTES:
                raise ConsultaGeometriaErro('O conteúdo descompactado do KMZ excede o limite seguro.')
            candidatos = [item for item in infos if item.filename.lower().endswith('.kml') and not item.is_dir()]
            if not candidatos:
                raise ConsultaGeometriaErro('O KMZ não contém um arquivo KML.')
            # doc.kml é o padrão de muitos softwares GIS; caso contrário usa o primeiro KML.
            candidatos.sort(key=lambda item: (0 if item.filename.lower().endswith('doc.kml') else 1, item.filename.lower()))
            return archive.read(candidatos[0])
    except zipfile.BadZipFile as exc:
        raise ConsultaGeometriaErro('O arquivo KMZ é inválido ou está corrompido.') from exc


def _coordenadas(texto):
    pontos = []
    for token in str(texto or '').replace('\n', ' ').replace('\t', ' ').split():
        partes = token.split(',')
        if len(partes) < 2:
            continue
        try:
            lon = float(partes[0])
            lat = float(partes[1])
        except (TypeError, ValueError):
            continue
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ConsultaGeometriaErro('O KML possui coordenadas fora dos limites de latitude/longitude.')
        pontos.append([lon, lat])
        if len(pontos) > MAX_VERTICES:
            raise ConsultaGeometriaErro('A geometria possui vértices demais para uma consulta interativa.')

    if len(pontos) < 3:
        raise ConsultaGeometriaErro('O KML não possui um polígono válido.')
    if pontos[0] != pontos[-1]:
        pontos.append(pontos[0])
    if len(pontos) < 4:
        raise ConsultaGeometriaErro('O KML não possui um anel poligonal válido.')
    return pontos


def _primeiro_filho(elemento, nome):
    for item in elemento.iter():
        if item.tag.rsplit('}', 1)[-1] == nome:
            return item
    return None


def _aneis_poligono(poligono):
    externo = None
    internos = []
    for boundary in list(poligono):
        local = boundary.tag.rsplit('}', 1)[-1]
        if local not in {'outerBoundaryIs', 'innerBoundaryIs'}:
            continue
        coords = _primeiro_filho(boundary, 'coordinates')
        if coords is None or not (coords.text or '').strip():
            continue
        ring = _coordenadas(coords.text)
        if local == 'outerBoundaryIs' and externo is None:
            externo = ring
        elif local == 'innerBoundaryIs':
            internos.append(ring)
    if externo is None:
        coords = _primeiro_filho(poligono, 'coordinates')
        if coords is not None and (coords.text or '').strip():
            externo = _coordenadas(coords.text)
    if externo is None:
        return None
    return [externo, *internos]


def _parsear_xml_kml(data):
    try:
        return ET.fromstring(data)
    except ET.ParseError as erro_original:
        # Alguns sistemas geram KML em Windows-1252/ANSI
        # mesmo declarando UTF-8 no cabeçalho.
        try:
            data.decode('utf-8')
        except UnicodeDecodeError:
            try:
                texto = data.decode('cp1252')
                return ET.fromstring(texto.encode('utf-8'))
            except (UnicodeDecodeError, ET.ParseError):
                pass

        raise ConsultaGeometriaErro(
            'O arquivo KML não pôde ser interpretado.'
        ) from erro_original


def geometria_de_kml_bytes(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ConsultaGeometriaErro('O KML contém uma declaração XML não permitida.')

    raiz = _parsear_xml_kml(data)

    polygons = []
    for elemento in raiz.iter():
        if elemento.tag.rsplit('}', 1)[-1] != 'Polygon':
            continue
        rings = _aneis_poligono(elemento)
        if rings:
            polygons.append(rings)

    if not polygons:
        raise ConsultaGeometriaErro('Nenhum polígono foi encontrado no KML.')

    # Cada Polygon do KML é validado separadamente e depois unido.
    # Isso permite KMLs com glebas duplicadas ou parcialmente sobrepostas,
    # que não formariam um MultiPolygon válido se fossem apenas agrupadas.
    try:
        geos = None

        for rings in polygons:
            atual = GEOSGeometry(
                json.dumps({
                    'type': 'Polygon',
                    'coordinates': rings,
                }),
                srid=4326,
            )

            if atual.empty or not atual.valid:
                raise ConsultaGeometriaErro(
                    'O KML contém um polígono inválido ou vazio.'
                )

            geos = atual if geos is None else geos.union(atual)

    except ConsultaGeometriaErro:
        raise
    except Exception as exc:
        raise ConsultaGeometriaErro(
            'A geometria do KML é inválida.'
        ) from exc

    if (
        geos is None
        or geos.empty
        or geos.geom_type not in {'Polygon', 'MultiPolygon'}
        or not geos.valid
    ):
        raise ConsultaGeometriaErro(
            'A gleba do KML precisa ser um polígono válido e não vazio.'
        )

    return json.loads(geos.geojson)


def _nome_placemark(elemento, fallback):
    for filho in list(elemento):
        if filho.tag.rsplit('}', 1)[-1] != 'name':
            continue
        nome = ' '.join((filho.text or '').split()).strip()
        if nome:
            return nome[:80]
    return fallback


def _glebas_individuais_de_kml_bytes(data):
    raiz = _parsear_xml_kml(data)

    placemarks = [
        elemento for elemento in raiz.iter()
        if elemento.tag.rsplit('}', 1)[-1] == 'Placemark'
    ]

    alvos = placemarks if placemarks else [raiz]
    glebas = []

    for indice_alvo, alvo in enumerate(alvos, start=1):
        nome_base = _nome_placemark(alvo, f'Gleba {indice_alvo}')
        indice_poligono = 0

        for elemento in alvo.iter():
            if elemento.tag.rsplit('}', 1)[-1] != 'Polygon':
                continue

            rings = _aneis_poligono(elemento)
            if not rings:
                continue

            indice_poligono += 1
            nome = (
                nome_base
                if indice_poligono == 1
                else f'{nome_base} {indice_poligono}'
            )

            try:
                geos = GEOSGeometry(
                    json.dumps({
                        'type': 'Polygon',
                        'coordinates': rings,
                    }),
                    srid=4326,
                )
            except Exception as exc:
                raise ConsultaGeometriaErro(
                    f'A geometria da gleba "{nome}" é inválida.'
                ) from exc

            if geos.empty or not geos.valid:
                raise ConsultaGeometriaErro(
                    f'A geometria da gleba "{nome}" é inválida.'
                )

            glebas.append({
                'type': 'Feature',
                'properties': {
                    'name': nome,
                    'confronta_nome': nome,
                },
                'geometry': json.loads(geos.geojson),
            })

            if len(glebas) > 100:
                raise ConsultaGeometriaErro(
                    'O KML possui mais de 100 polígonos para uma consulta interativa.'
                )

    return glebas


def geometria_e_glebas_de_upload(uploaded_file):
    nome = str(getattr(uploaded_file, 'name', '') or '').lower().strip()

    if not (nome.endswith('.kml') or nome.endswith('.kmz')):
        raise ConsultaGeometriaErro('Envie um arquivo KML ou KMZ.')

    data = _upload_bytes(uploaded_file)

    if nome.endswith('.kmz'):
        data = _kml_de_kmz(data)

    geometria = geometria_de_kml_bytes(data)
    glebas = _glebas_individuais_de_kml_bytes(data)

    return geometria, glebas


def geometria_de_upload(uploaded_file):
    geometria, _ = geometria_e_glebas_de_upload(uploaded_file)
    return geometria


def geometria_de_geojson_texto(texto):
    try:
        raw = json.loads(texto)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConsultaGeometriaErro('A geometria desenhada é inválida.') from exc

    if isinstance(raw, dict) and raw.get('type') == 'Feature':
        raw = raw.get('geometry')
    if not isinstance(raw, dict) or raw.get('type') not in {'Polygon', 'MultiPolygon'}:
        raise ConsultaGeometriaErro('Desenhe uma área poligonal para realizar a consulta.')
    try:
        geos = GEOSGeometry(json.dumps(raw), srid=4326)
    except Exception as exc:
        raise ConsultaGeometriaErro('A geometria desenhada é inválida.') from exc
    if geos.empty or not geos.valid:
        raise ConsultaGeometriaErro('A geometria desenhada precisa ser válida e não vazia.')
    return json.loads(geos.geojson)
