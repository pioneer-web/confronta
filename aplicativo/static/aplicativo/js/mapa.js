(function () {
    'use strict';

    const mapElement = document.getElementById('map');
    if (!mapElement || typeof L === 'undefined') return;

    // MÓDULO 2 — v0.3.5
    // A Área Aplicativo utiliza somente imagem de satélite.
    // maxNativeZoom limita as requisições ao nível seguro observado do serviço;
    // maxZoom permite aproximação adicional apenas por reamostragem do último tile,
    // evitando solicitar níveis sem imagem e exibir “sem mapa”.
    const MAX_SATELLITE_NATIVE_ZOOM = 17;
    const MAX_SATELLITE_ZOOM = 19;

    const map = L.map(mapElement, {
        zoomControl: true,
        rotate: true,
        minZoom: 3,
        maxZoom: MAX_SATELLITE_ZOOM,
        zoomSnap: 0.5,
        zoomDelta: 0.5
    }).setView([-14.2, -51.9], 4);

    // HOME v14 — zoom volta ao canto superior esquerdo do mapa. A barra
    // lateral do CONFRONTA fica fora da área cartográfica, abaixo do topo.
    if (map.zoomControl && typeof map.zoomControl.setPosition === 'function') {
        map.zoomControl.setPosition('topleft');
    }

    const satellite = L.tileLayer(
        'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        {
            minZoom: 0,
            maxNativeZoom: MAX_SATELLITE_NATIVE_ZOOM,
            maxZoom: MAX_SATELLITE_ZOOM,
            updateWhenIdle: true,
            keepBuffer: 3,
            attribution: 'Tiles &copy; Esri, Maxar, Earthstar Geographics e comunidade GIS'
        }
    ).addTo(map);

    satellite.on('tileerror', function (event) {
        console.warn('CONFRONTA: falha ao carregar tile de satélite Esri.', event && event.coords ? event.coords : event);
    });

    const rawData = document.getElementById('consulta-territorial-data');
    const configElement = document.getElementById('app-config');
    const canDraw = Boolean(configElement && configElement.dataset.canDraw === '1');
    const carCode = (configElement && configElement.dataset.car) || 'CAR';
    const layers = {};
    let perimeter = null;
    let activeFullFeaturePreview = null;

    const palette = {
        perimetro: { color: '#FFFFFF', weight: 2.5, fillOpacity: 0.07, opacity: 1, fillColor: '#54d38a' },
        app: { color: '#2b83cf', weight: 2.4, fillOpacity: 0.22, opacity: 0.98 },
        reserva_legal: { color: '#1b7a35', weight: 2.3, fillOpacity: 0.26, opacity: 0.98 },
        vegetacao_nativa: { color: '#22a35f', weight: 2.3, fillOpacity: 0.24, opacity: 0.98 },
        area_consolidada: { color: '#d19a24', weight: 2.25, fillOpacity: 0.22, opacity: 0.98 },
        area_pousio: { color: '#b67a18', weight: 2.2, fillOpacity: 0.20, opacity: 0.97 },
        hidrografia: { color: '#0f8fd6', weight: 2.45, fillOpacity: 0.24, opacity: 0.98 },
        servidao_administrativa: { color: '#5f6b7a', weight: 2.15, fillOpacity: 0.18, opacity: 0.96 },
        uso_restrito: { color: '#99622a', weight: 2.2, fillOpacity: 0.22, opacity: 0.97 },
        ext_ibama: { color: '#ef4444', weight: 2.55, fillOpacity: 0.28, opacity: 0.98 },
        ext_prodes: { color: '#f97316', weight: 2.6, fillOpacity: 0.32, opacity: 0.99 },
        ext_prodes_desmatamento: { color: '#dc2626', weight: 2.65, fillOpacity: 0.34, opacity: 0.99 },
        ext_prodes_queimada: { color: '#f59e0b', weight: 2.55, fillOpacity: 0.30, opacity: 0.99 },
        ext_assentamentos: { color: '#f59e0b', weight: 2.35, fillOpacity: 0.26, opacity: 0.98 },
        ext_quilombolas: { color: '#9333ea', weight: 2.35, fillOpacity: 0.26, opacity: 0.98 },
        ext_funai: { color: '#7c3aed', weight: 2.45, fillOpacity: 0.27, opacity: 0.99 },
        ext_icmbio_embargo: { color: '#be123c', weight: 2.55, fillOpacity: 0.28, opacity: 0.99 },
        ext_apa: { color: '#16a34a', weight: 2.35, fillOpacity: 0.24, opacity: 0.98 },
        ext_sicor: { color: '#3b82f6', weight: 2.65, fillOpacity: 0.28, opacity: 0.99 },
        ext_outros_car: { color: '#0891b2', weight: 2.55, fillOpacity: 0.22, opacity: 0.99 }
    };

    const GIS_STANDARD_COLORS = [
        { value: '#FFFFFF', label: 'Branco' },
        { value: '#1D4ED8', label: 'Azul' },
        { value: '#0F8FD6', label: 'Azul claro' },
        { value: '#16A34A', label: 'Verde' },
        { value: '#D4A017', label: 'Amarelo' },
        { value: '#F97316', label: 'Laranja' },
        { value: '#DC2626', label: 'Vermelho' },
        { value: '#7C3AED', label: 'Roxo' },
        { value: '#0891B2', label: 'Ciano' }
    ];

    function normalizeHexColor(value, fallback) {
        const raw = String(value || fallback || '').trim().toUpperCase();
        return /^#[0-9A-F]{6}$/.test(raw) ? raw : String(fallback || '#0B7567').toUpperCase();
    }

    function styleForKey(key) {
        const base = palette[key] || { color: '#4FA36A', weight: 1.9, fillOpacity: 0.16, opacity: 0.92 };
        return { ...base };
    }

    function currentLayerColor(key, fallback) {
        return normalizeHexColor((palette[key] && palette[key].color) || fallback || '#0B7567', '#0B7567');
    }

    function pointStyleFromVectorStyle(style) {
        return {
            radius: 4,
            color: style.color || '#4FA36A',
            weight: Math.max(1.5, Number(style.weight || 2) - 0.6),
            opacity: Math.min(1, Number(style.opacity || 0.96)),
            fillColor: style.color || '#4FA36A',
            fillOpacity: Math.max(0.40, Math.min(0.78, Number(style.fillOpacity || 0.24) + 0.20))
        };
    }

    function applyLayerStyleObject(targetLayer, style) {
        if (!targetLayer || !style) return;
        if (typeof targetLayer.setStyle === 'function') targetLayer.setStyle(style);
        if (typeof targetLayer.eachLayer === 'function') {
            targetLayer.eachLayer((child) => {
                if (!child || typeof child.setStyle !== 'function') return;
                if (typeof L !== 'undefined' && child instanceof L.CircleMarker) child.setStyle(pointStyleFromVectorStyle(style));
                else child.setStyle(style);
            });
        }
    }

    function applyLayerColor(key, color) {
        if (!key || !layers[key]) return;
        const hex = normalizeHexColor(color, currentLayerColor(key, '#0B7567'));
        if (!palette[key]) palette[key] = { color: hex, weight: 1.9, fillOpacity: 0.16, opacity: 0.92 };
        palette[key].color = hex;
        const style = styleForKey(key);
        applyLayerStyleObject(layers[key], style);
    }

    function fitLayer(key) {
        const layer = layers[key];
        if (!layer || typeof layer.getBounds !== 'function') return;
        const bounds = layer.getBounds();
        if (!bounds || !bounds.isValid()) return;
        map.fitBounds(bounds, { padding: [22, 22], maxZoom: MAX_SATELLITE_ZOOM, animate: false });
    }

    function styleForFeatureLayer(layerKey, featureLayer, emphasize) {
        const style = styleForKey(layerKey);
        const custom = featureLayer && featureLayer._confrontaCustomColor ? normalizeHexColor(featureLayer._confrontaCustomColor, style.color) : style.color;
        style.color = custom;
        if (!('fillColor' in style) || !style.fillColor) style.fillColor = custom;
        else style.fillColor = custom;
        if (emphasize) {
            style.weight = Number(style.weight || 2) + 0.45;
            style.fillOpacity = Math.min(0.42, Number(style.fillOpacity || 0.2) + 0.08);
        }
        if (featureLayer && featureLayer._confrontaHidden) {
            style.opacity = 0;
            style.fillOpacity = 0;
        }
        return style;
    }

    function applyStyleToFeatureLayer(featureLayer, layerKey, emphasize) {
        if (!featureLayer || typeof featureLayer.setStyle !== 'function') return;
        const style = styleForFeatureLayer(layerKey, featureLayer, emphasize);
        if (typeof L !== 'undefined' && featureLayer instanceof L.CircleMarker) featureLayer.setStyle(pointStyleFromVectorStyle(style));
        else featureLayer.setStyle(style);
        if (featureLayer._path) featureLayer._path.style.pointerEvents = featureLayer._confrontaHidden ? 'none' : 'auto';
    }

    function clearFullFeaturePreview() {
        if (activeFullFeaturePreview && map.hasLayer(activeFullFeaturePreview)) map.removeLayer(activeFullFeaturePreview);
        activeFullFeaturePreview = null;
    }

    function showFullFeaturePreview(featureLayer, layerKey, feature) {
        clearFullFeaturePreview();
        const fullGeometry = feature && feature.properties && feature.properties._confronta_full_geometry;
        if (!fullGeometry) {
            if (featureLayer && typeof featureLayer.getBounds === 'function') {
                const bounds = featureLayer.getBounds();
                if (bounds && bounds.isValid()) map.fitBounds(bounds, { padding: [22, 22], maxZoom: MAX_SATELLITE_ZOOM, animate: false });
            }
            return;
        }
        activeFullFeaturePreview = L.geoJSON({ type: 'Feature', properties: {}, geometry: fullGeometry }, {
            style: function () {
                const style = styleForFeatureLayer(layerKey, featureLayer, true);
                return style;
            },
            pointToLayer: function (ft, latlng) {
                return L.circleMarker(latlng, pointStyleFromVectorStyle(styleForFeatureLayer(layerKey, featureLayer, true)));
            }
        }).addTo(map);
        if (typeof activeFullFeaturePreview.bringToFront === 'function') activeFullFeaturePreview.bringToFront();
        const bounds = activeFullFeaturePreview.getBounds();
        if (bounds && bounds.isValid()) map.fitBounds(bounds, { padding: [22, 22], maxZoom: MAX_SATELLITE_ZOOM, animate: false });
    }

    function applyFeatureColor(featureLayer, layerKey, color) {
        if (!featureLayer) return;
        featureLayer._confrontaCustomColor = normalizeHexColor(color, currentLayerColor(layerKey, '#0B7567'));
        applyStyleToFeatureLayer(featureLayer, layerKey, false);
        if (featureLayer._confrontaFullPreviewOpen) showFullFeaturePreview(featureLayer, layerKey, featureLayer.feature || null);
    }

    function hideFeatureLayer(featureLayer, layerKey) {
        if (!featureLayer) return;
        featureLayer._confrontaHidden = true;
        featureLayer.closePopup && featureLayer.closePopup();
        applyStyleToFeatureLayer(featureLayer, layerKey, false);
        if (featureLayer._confrontaFullPreviewOpen) {
            featureLayer._confrontaFullPreviewOpen = false;
            clearFullFeaturePreview();
        }
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function humanizeKey(value) {
        return String(value || '')
            .replaceAll('_', ' ')
            .replace(/\b\w/g, (letter) => letter.toUpperCase());
    }

    function formatValue(value) {
        if (value === null || value === undefined || value === '') return 'Não informado';
        if (typeof value === 'number' && Number.isFinite(value)) {
            return value.toLocaleString('pt-BR', { maximumFractionDigits: 4 });
        }
        return String(value);
    }

    function compactProperties(feature) {
        const props = (feature && feature.properties) || {};
        const ignored = new Set(['geom', 'geometry', 'the_geom', 'wkb_geometry']);
        return Object.entries(props)
            .filter(([key, value]) => !ignored.has(String(key).toLowerCase()) && value !== null && value !== '')
            .slice(0, 4);
    }

    function selectedAreaHa(feature, layerData) {
        try {
            if (window.turf && typeof window.turf.area === 'function' && feature && feature.geometry) {
                const area = window.turf.area(feature) / 10000;
                if (Number.isFinite(area) && area > 0) return area;
            }
        } catch (error) {
            // Turf é opcional no plano Básico. O popup continua funcional sem ele.
        }
        if (layerData && Array.isArray(layerData.features) && layerData.features.length === 1) {
            const total = Number(layerData.total_area_ha);
            if (Number.isFinite(total) && total > 0) return total;
        }
        const props = (feature && feature.properties) || {};
        for (const [key, rawValue] of Object.entries(props)) {
            const normalizedKey = String(key).toLowerCase();
            if (!normalizedKey.includes('area') || !normalizedKey.includes('ha')) continue;
            const value = Number(String(rawValue).replace(',', '.'));
            if (Number.isFinite(value) && value > 0) return value;
        }
        return null;
    }

    function coordinatesToKml(coordinates) {
        return (coordinates || []).map((coord) => `${Number(coord[0])},${Number(coord[1])},0`).join(' ');
    }

    function polygonToKml(rings) {
        if (!Array.isArray(rings) || !rings.length) return '';
        const outer = `<outerBoundaryIs><LinearRing><coordinates>${coordinatesToKml(rings[0])}</coordinates></LinearRing></outerBoundaryIs>`;
        const inners = rings.slice(1).map((ring) => `<innerBoundaryIs><LinearRing><coordinates>${coordinatesToKml(ring)}</coordinates></LinearRing></innerBoundaryIs>`).join('');
        return `<Polygon><tessellate>1</tessellate>${outer}${inners}</Polygon>`;
    }

    function geometryToKml(geometry) {
        if (!geometry) return '';
        switch (geometry.type) {
            case 'Polygon': return polygonToKml(geometry.coordinates);
            case 'MultiPolygon': return `<MultiGeometry>${(geometry.coordinates || []).map(polygonToKml).join('')}</MultiGeometry>`;
            case 'LineString': return `<LineString><tessellate>1</tessellate><coordinates>${coordinatesToKml(geometry.coordinates)}</coordinates></LineString>`;
            case 'MultiLineString': return `<MultiGeometry>${(geometry.coordinates || []).map((line) => `<LineString><tessellate>1</tessellate><coordinates>${coordinatesToKml(line)}</coordinates></LineString>`).join('')}</MultiGeometry>`;
            case 'Point': return `<Point><coordinates>${coordinatesToKml([geometry.coordinates])}</coordinates></Point>`;
            case 'MultiPoint': return `<MultiGeometry>${(geometry.coordinates || []).map((point) => `<Point><coordinates>${coordinatesToKml([point])}</coordinates></Point>`).join('')}</MultiGeometry>`;
            default: return '';
        }
    }

    function downloadFeatureKml(feature, label) {
        if (!feature || !feature.geometry) return;
        const geometry = geometryToKml(feature.geometry);
        if (!geometry) return;
        const safeLabel = String(label || 'area').replace(/[<>:&"']/g, ' ').trim() || 'area';
        const xmlLabel = escapeHtml(safeLabel);
        const kml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>${xmlLabel}</name><Placemark><name>${xmlLabel}</name>${geometry}</Placemark></Document></kml>`;
        const blob = new Blob([kml], { type: 'application/vnd.google-earth.kml+xml;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `${safeLabel.toLowerCase().replace(/[^a-z0-9áàâãéèêíïóôõöúçñ_-]+/gi, '-').replace(/^-+|-+$/g, '') || 'area'}.kml`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 0);
    }

    function buildTerritorialPopup(feature, label, color, kind, layerData, layerKey, featureLayer) {
        const root = document.createElement('section');
        root.className = 'cf-feature-popup';
        const isOtherCar = layerKey === 'ext_outros_car';
        const selectedColor = featureLayer ? normalizeHexColor(featureLayer._confrontaCustomColor || currentLayerColor(layerKey, color || '#0B7567'), currentLayerColor(layerKey, '#0B7567')) : currentLayerColor(layerKey, color || '#0B7567');

        const head = document.createElement('header');
        head.className = 'cf-feature-popup-head';
        const dot = document.createElement('span');
        dot.className = 'cf-feature-popup-dot';
        dot.style.backgroundColor = selectedColor;
        const title = document.createElement('div');
        title.className = 'cf-feature-popup-title';
        const titleStrong = document.createElement('strong');
        titleStrong.textContent = label || 'Área territorial';
        const subtitle = document.createElement('span');
        subtitle.textContent = kind || 'Camada territorial';
        title.append(titleStrong, subtitle);
        head.append(dot, title);

        const body = document.createElement('div');
        body.className = 'cf-feature-popup-body';
        const metrics = document.createElement('div');
        metrics.className = 'cf-feature-popup-metrics';

        const areaMetric = document.createElement('div');
        areaMetric.className = 'cf-feature-popup-metric';
        const areaLabel = document.createElement('span');
        areaLabel.textContent = 'Área selecionada';
        const areaValue = document.createElement('strong');
        const area = selectedAreaHa(feature, layerData);
        areaValue.textContent = area === null ? 'Geometria disponível' : `${area.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ha`;
        areaMetric.append(areaLabel, areaValue);

        const sourceMetric = document.createElement('div');
        sourceMetric.className = 'cf-feature-popup-metric';
        const sourceLabel = document.createElement('span');
        sourceLabel.textContent = isOtherCar ? 'Outro CAR' : 'Tipo';
        const sourceValue = document.createElement('strong');
        sourceValue.textContent = isOtherCar ? (feature?.properties?.cod_imovel || 'CAR sobreposto') : (kind || 'Camada territorial');
        sourceMetric.append(sourceLabel, sourceValue);
        metrics.append(areaMetric, sourceMetric);
        body.appendChild(metrics);

        const carRow = document.createElement('div');
        carRow.className = 'cf-feature-popup-car-line';
        const carLabel = document.createElement('span');
        carLabel.textContent = isOtherCar ? 'CAR consultado' : 'CAR consultado';
        const carValue = document.createElement('strong');
        carValue.textContent = carCode;
        carValue.title = carCode;
        carRow.append(carLabel, carValue);
        body.appendChild(carRow);

        if (isOtherCar && feature?.properties?.cod_imovel) {
            const selectedCarRow = document.createElement('div');
            selectedCarRow.className = 'cf-feature-popup-car-line';
            const selectedCarLabel = document.createElement('span');
            selectedCarLabel.textContent = 'CAR selecionado';
            const selectedCarValue = document.createElement('strong');
            selectedCarValue.textContent = feature.properties.cod_imovel;
            selectedCarValue.title = feature.properties.cod_imovel;
            selectedCarRow.append(selectedCarLabel, selectedCarValue);
            body.appendChild(selectedCarRow);
        }

        const props = compactProperties(feature).filter(([key]) => key !== '_confronta_full_geometry');
        if (props.length) {
            const details = document.createElement('div');
            details.className = 'cf-feature-popup-details';
            props.forEach(([key, value]) => {
                const row = document.createElement('div');
                const name = document.createElement('span');
                name.textContent = humanizeKey(key);
                const content = document.createElement('strong');
                content.textContent = formatValue(value);
                row.append(name, content);
                details.appendChild(row);
            });
            body.appendChild(details);
        }

        const colorSection = document.createElement('div');
        colorSection.className = 'cf-feature-popup-color-section';
        const colorSectionLabel = document.createElement('span');
        colorSectionLabel.className = 'cf-feature-popup-color-label';
        colorSectionLabel.textContent = isOtherCar ? 'Cor deste CAR' : 'Cor desta área';
        const swatches = document.createElement('div');
        swatches.className = 'cf-feature-popup-swatches';
        GIS_STANDARD_COLORS.forEach((entry) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'cf-feature-popup-swatch';
            if (normalizeHexColor(entry.value, '#0B7567') === selectedColor) button.classList.add('is-active');
            if (normalizeHexColor(entry.value, '#0B7567') === '#FFFFFF') button.classList.add('is-light');
            button.style.backgroundColor = entry.value;
            button.title = `Usar ${entry.label}`;
            button.setAttribute('aria-label', `Usar cor ${entry.label}`);
            button.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                applyFeatureColor(featureLayer, layerKey, entry.value);
                dot.style.backgroundColor = normalizeHexColor(entry.value, '#0B7567');
                swatches.querySelectorAll('.cf-feature-popup-swatch').forEach((item) => {
                    item.classList.toggle('is-active', item === button);
                });
            });
            swatches.appendChild(button);
        });
        colorSection.append(colorSectionLabel, swatches);
        body.appendChild(colorSection);

        const actions = document.createElement('div');
        actions.className = 'cf-feature-popup-actions';

        const full = document.createElement('button');
        full.type = 'button';
        full.className = 'cf-feature-popup-action is-secondary';
        full.innerHTML = isOtherCar
            ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5"></path><path d="M16 3h5v5"></path><path d="M21 16v5h-5"></path><path d="M8 21H3v-5"></path></svg><span>Mostrar CAR completo</span>'
            : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5"></path><path d="M16 3h5v5"></path><path d="M21 16v5h-5"></path><path d="M8 21H3v-5"></path></svg><span>Mostrar área completa</span>';
        full.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (featureLayer) featureLayer._confrontaFullPreviewOpen = true;
            showFullFeaturePreview(featureLayer, layerKey, feature);
        });

        const hide = document.createElement('button');
        hide.type = 'button';
        hide.className = 'cf-feature-popup-action is-muted';
        hide.innerHTML = isOtherCar
            ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18"></path><path d="M10.6 10.7a3 3 0 0 0 4.2 4.2"></path><path d="M9.9 5.1A10.9 10.9 0 0 1 12 5c5.3 0 9.3 4 10 7-.2.8-.8 2-2 3.2"></path><path d="M6.2 6.3C4 8 2.6 10 2 12c.7 3 4.7 7 10 7 1.5 0 2.8-.3 4-.8"></path></svg><span>Ocultar este CAR</span>'
            : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18"></path><path d="M10.6 10.7a3 3 0 0 0 4.2 4.2"></path><path d="M9.9 5.1A10.9 10.9 0 0 1 12 5c5.3 0 9.3 4 10 7-.2.8-.8 2-2 3.2"></path><path d="M6.2 6.3C4 8 2.6 10 2 12c.7 3 4.7 7 10 7 1.5 0 2.8-.3 4-.8"></path></svg><span>Ocultar esta área</span>';
        hide.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            hideFeatureLayer(featureLayer, layerKey);
            map.closePopup();
        });

        actions.append(full, hide);
        body.appendChild(actions);

        const kml = document.createElement('button');
        kml.type = 'button';
        kml.className = 'cf-feature-popup-kml';
        kml.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12"></path><path d="m7.5 10.5 4.5 4.5 4.5-4.5"></path><path d="M5 20h14"></path></svg><span>Baixar KML desta área</span>';
        kml.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            downloadFeatureKml(feature, label);
        });
        body.appendChild(kml);
        root.append(head, body);
        return root;
    }

    let consulta = null;
    if (rawData) {
        try {
            consulta = JSON.parse(rawData.textContent);
        } catch (error) {
            console.error('CONFRONTA: dados territoriais inválidos.', error);
        }
    }

    function enquadrarCar() {
        if (!perimeter) return;
        const bounds = perimeter.getBounds();
        if (!bounds.isValid()) return;
        map.fitBounds(bounds, {
            padding: [18, 18],
            maxZoom: MAX_SATELLITE_ZOOM,
            animate: false
        });
    }

    function addGeoJsonLayer(key, layerData, visibleByDefault) {
        if (!layerData || !layerData.disponivel || !Array.isArray(layerData.features) || !layerData.features.length) return;
        const group = L.geoJSON({ type: 'FeatureCollection', features: layerData.features }, {
            style: function () {
                return styleForKey(key);
            },
            pointToLayer: function (feature, latlng) {
                return L.circleMarker(latlng, pointStyleFromVectorStyle(styleForKey(key)));
            },
            onEachFeature: function (feature, layer) {
                applyStyleToFeatureLayer(layer, key, false);
                layer.bindPopup(() => buildTerritorialPopup(
                    feature,
                    layerData.label || key,
                    currentLayerColor(key, '#0B7567'),
                    key.startsWith('ext_') ? 'Base territorial externa' : 'Área da camada',
                    layerData,
                    key,
                    layer
                ), {
                    maxWidth: 360,
                    minWidth: 270,
                    closeButton: true,
                    autoPanPadding: [28, 28],
                    className: 'confronta-feature-leaflet-popup'
                });
            }
        });
        layers[key] = group;
        applyLayerStyleObject(group, styleForKey(key));
        if (visibleByDefault) group.addTo(map);
    }

    if (consulta && consulta.imovel && consulta.imovel.geometry) {
        perimeter = L.geoJSON({
            type: 'Feature',
            properties: { car: consulta.imovel.cod_imovel },
            geometry: consulta.imovel.geometry
        }, {
            style: styleForKey('perimetro'),
            onEachFeature: function (feature, layer) {
                const carFeature = {
                    type: 'Feature',
                    properties: {
                        municipio: consulta.imovel.municipio || '',
                        uf: consulta.imovel.uf || '',
                        situacao: consulta.imovel.situacao_apresentacao || ''
                    },
                    geometry: feature.geometry
                };
                applyStyleToFeatureLayer(layer, 'perimetro', false);
                layer.bindPopup(() => buildTerritorialPopup(
                    carFeature,
                    'Perímetro do CAR',
                    currentLayerColor('perimetro', '#FFFFFF'),
                    'Imóvel consultado',
                    { total_area_ha: consulta.imovel.area_total_ha, features: [carFeature] },
                    'perimetro',
                    layer
                ), {
                    maxWidth: 360,
                    minWidth: 270,
                    closeButton: true,
                    autoPanPadding: [28, 28],
                    className: 'confronta-feature-leaflet-popup'
                });
            }
        }).addTo(map);
        layers.perimetro = perimeter;

        Object.entries(consulta.camadas || {}).forEach(([key, layerData]) => {
            addGeoJsonLayer(key, layerData, true);
        });

        Object.entries(consulta.camadas_externas || {}).forEach(([key, layerData]) => {
            addGeoJsonLayer(`ext_${key}`, layerData, false);
        });

        enquadrarCar();
    }

    function setLayerVisible(key, visible) {
        const layer = layers[key];
        if (!layer) return;
        if (visible) {
            if (!map.hasLayer(layer)) layer.addTo(map);
        } else if (map.hasLayer(layer)) {
            map.removeLayer(layer);
        }

        document.querySelectorAll(`.layer-toggle[data-layer="${CSS.escape(key)}"]`).forEach((toggle) => {
            if (!toggle.disabled) toggle.checked = visible;
        });
    }

    document.querySelectorAll('.layer-toggle').forEach((toggle) => {
        toggle.addEventListener('change', function () {
            setLayerVisible(this.dataset.layer, this.checked);
        });
    });

    const fitButton = document.getElementById('fit-car');
    if (fitButton) fitButton.addEventListener('click', enquadrarCar);

    function currentBearing() {
        return typeof map.getBearing === 'function' ? map.getBearing() : 0;
    }

    function applyBearing(value) {
        if (typeof map.setBearing !== 'function') return;
        let bearing = value % 360;
        if (bearing < 0) bearing += 360;
        map.setBearing(bearing);
    }

    const rotateLeft = document.getElementById('rotate-left');
    const rotateRight = document.getElementById('rotate-right');
    const northUp = document.getElementById('north-up');
    if (rotateLeft) rotateLeft.addEventListener('click', () => applyBearing(currentBearing() - 15));
    if (rotateRight) rotateRight.addEventListener('click', () => applyBearing(currentBearing() + 15));
    if (northUp) northUp.addEventListener('click', () => applyBearing(0));

    // HOME v14 — controle compacto de rotação do mapa/CAR. Só é exibido
    // quando existe um CAR carregado e o plugin de rotação está disponível.
    if (perimeter && typeof map.setBearing === 'function' && typeof L.control === 'function') {
        const rotationControl = L.control({ position: 'topleft' });
        rotationControl.onAdd = function () {
            const container = L.DomUtil.create('div', 'leaflet-bar confronta-rotation-control');
            container.setAttribute('aria-label', 'Rotacionar mapa');

            const leftButton = L.DomUtil.create('button', 'confronta-rotation-button', container);
            leftButton.type = 'button';
            leftButton.title = 'Girar 15° à esquerda';
            leftButton.setAttribute('aria-label', 'Girar 15 graus à esquerda');
            leftButton.innerHTML = '&#8634;';

            const northButton = L.DomUtil.create('button', 'confronta-rotation-button confronta-rotation-north', container);
            northButton.type = 'button';
            northButton.title = 'Voltar para o norte';
            northButton.setAttribute('aria-label', 'Voltar para o norte');
            northButton.textContent = 'N';

            const rightButton = L.DomUtil.create('button', 'confronta-rotation-button', container);
            rightButton.type = 'button';
            rightButton.title = 'Girar 15° à direita';
            rightButton.setAttribute('aria-label', 'Girar 15 graus à direita');
            rightButton.innerHTML = '&#8635;';

            L.DomEvent.disableClickPropagation(container);
            L.DomEvent.disableScrollPropagation(container);
            L.DomEvent.on(leftButton, 'click', () => applyBearing(currentBearing() - 15));
            L.DomEvent.on(northButton, 'click', () => applyBearing(0));
            L.DomEvent.on(rightButton, 'click', () => applyBearing(currentBearing() + 15));
            return container;
        };
        rotationControl.addTo(map);
    }

    // Impede que qualquer rotina externa deixe o mapa além do limite configurado.
    map.on('zoomend', function () {
        if (map.getZoom() > MAX_SATELLITE_ZOOM) map.setZoom(MAX_SATELLITE_ZOOM);
    });

    window.CONFRONTA_MAP_CONTEXT = {
        map,
        consulta,
        layers,
        perimeter,
        satellite,
        carCode,
        canDraw,
        maxNativeZoom: MAX_SATELLITE_NATIVE_ZOOM,
        maxZoom: MAX_SATELLITE_ZOOM,
        setLayerVisible,
        fitCar: enquadrarCar
    };

    window.setTimeout(() => map.invalidateSize(), 80);
})();
