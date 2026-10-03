(function () {
    'use strict';

    // MÓDULO 2 — v0.3.5
    // Glebas ficam somente no navegador (sessionStorage). Nenhuma geometria
    // desenhada/importada por esta ferramenta é gravada no PostgreSQL/PostGIS.
    const context = window.CONFRONTA_MAP_CONTEXT;
    if (!context || !context.canDraw || !context.consulta || typeof L === 'undefined') return;
    if (typeof L.Draw === 'undefined' || typeof turf === 'undefined') return;

    const map = context.map;
    const consulta = context.consulta;
    const carCode = context.carCode || 'CAR';
    const configElement = document.getElementById('app-config');
    const userId = (configElement && configElement.dataset.userId) || 'anonimo';

    const startButton = document.getElementById('start-new-gleba');
    const importButton = document.getElementById('import-gleba-button');
    const colorPicker = document.getElementById('gleba-color-picker');
    const importInput = document.getElementById('import-gleba-file');
    const importStatus = document.getElementById('gleba-import-status');
    const pendingName = document.getElementById('gleba-pending-name');
    const pendingArea = document.getElementById('gleba-pending-area');
    const pendingSave = document.getElementById('gleba-pending-save');
    const pendingDiscard = document.getElementById('gleba-pending-discard');
    const listElement = document.getElementById('gleba-list');
    const totalAreaElement = document.getElementById('gleba-area');
    const statusElement = document.getElementById('gleba-status');
    const pendingSavePanel = document.getElementById('gleba-pending-save-panel');
    const overlapAlert = document.getElementById('overlap-alert');
    const liveAreaBox = document.getElementById('gleba-live-area-map');
    const liveAreaValue = document.getElementById('gleba-live-area-value');
    const editControls = document.getElementById('gleba-edit-controls');
    const editSaveButton = document.getElementById('gleba-edit-save');
    const editCancelButton = document.getElementById('gleba-edit-cancel');
    const drawingHud = document.getElementById('gleba-drawing-hud');
    const drawingHudArea = document.getElementById('gleba-hud-area');
    const drawingHudColor = document.getElementById('gleba-hud-color');
    const drawUndo = document.getElementById('gleba-draw-undo');
    const drawFinish = document.getElementById('gleba-draw-finish');
    const drawCancel = document.getElementById('gleba-draw-cancel');

    const serverPropertyAlert = overlapAlert
        ? String(overlapAlert.dataset.serverMessage || overlapAlert.textContent || '').trim()
        : '';

    const ALLOWED_COLORS = {
        '#2563EB': 'Azul',
        '#0891B2': 'Ciano',
        '#EAB308': 'Amarelo',
        '#F97316': 'Laranja',
        '#DC2626': 'Vermelho',
        '#A855F7': 'Violeta',
        '#FFFFFF': 'Branco'
    };
    const DEFAULT_COLOR = '#2563EB';
    const STORAGE_VERSION = 2;
    const STORAGE_KEY = `confronta:modulo2:glebas:v${STORAGE_VERSION}:${userId}:${carCode}`;
    const MAX_IMPORT_BYTES = 5 * 1024 * 1024;
    const MAX_IMPORT_POLYGONS = 100;

    const glebasPane = map.getPane('confrontaGlebasPane') || map.createPane('confrontaGlebasPane');
    // Acima de todas as camadas vetoriais do mapa (até o SICOR em 470),
    // mas abaixo do markerPane padrão para manter os vértices arrastáveis.
    glebasPane.style.zIndex = '590';
    glebasPane.style.pointerEvents = 'auto';
    const glebasRenderer = L.Browser && L.Browser.svg
        ? L.svg({ pane: 'confrontaGlebasPane' })
        : L.canvas({ pane: 'confrontaGlebasPane' });
    const drawnItems = new L.FeatureGroup().addTo(map);
    let creditMapActive = false;
    window.addEventListener('confronta:glebas-visibility', function (event) {
        const visible = Boolean(event.detail && event.detail.visible);
        if (visible) {
            if (!map.hasLayer(drawnItems)) drawnItems.addTo(map);
        } else {
            drawnItems.eachLayer((layer) => layer.closePopup && layer.closePopup());
            if (map.hasLayer(drawnItems)) map.removeLayer(drawnItems);
        }
    });
    window.addEventListener('confronta:credit-map-mode', function (event) {
        const active = Boolean(event.detail && event.detail.active);
        creditMapActive = active;
        drawnItems.eachLayer((layer) => {
            if (!active) {
                if (layer.unbindTooltip) layer.unbindTooltip();
                return;
            }
            const meta = metadataFor(layer);
            if (meta.visivel === false) {
                if (layer.unbindTooltip) layer.unbindTooltip();
                return;
            }
            const area = formatAreaHa(areaHa(featureForLayer(layer)));
            const label = `${meta.nome} · ${area}`;
            layer.bindTooltip(label, {
                permanent: true,
                direction: 'center',
                className: 'credit-gleba-label',
                interactive: false,
                opacity: 1
            });
        });
    });
    window.addEventListener('confronta:credit-map-fit', function () {
        const bounds = context.perimeter && context.perimeter.getBounds
            ? context.perimeter.getBounds()
            : drawnItems.getBounds();
        if (context.perimeter && context.perimeter.getBounds && drawnItems.getLayers().length) {
            bounds.extend(drawnItems.getBounds());
        }
        if (bounds && bounds.isValid()) {
            const drawer = document.getElementById('territorial-side-panel');
            const drawerWidth = drawer && !drawer.hidden ? drawer.getBoundingClientRect().width : 0;
            const rightPadding = drawerWidth ? Math.min(drawerWidth + 36, map.getSize().x * 0.72) : 48;
            map.fitBounds(bounds, {
                paddingTopLeft: [36, 36],
                paddingBottomRight: [rightPadding, 36],
                maxZoom: context.maxNativeZoom || 17
            });
        }
    });
    window.addEventListener('confronta:credit-map-print-bounds', function (event) {
        if (!event.detail) return;
        const bounds = context.perimeter?.getBounds ? context.perimeter.getBounds() : L.latLngBounds();
        drawnItems.eachLayer((layer) => {
            if (metadataFor(layer).visivel === false || !layer.getBounds) return;
            bounds.extend(layer.getBounds());
        });
        if (bounds.isValid()) event.detail.bounds = bounds;
    });
    window.addEventListener('confronta:credit-map-print-check', (event) => {
        if (!event.detail) return;
        if (editingLayer) event.detail.blockedReason = 'Salve ou cancele a edição antes de imprimir o mapa.';
        else if (pendingLayer || drawHandler || workflowStep === 'name' || workflowStep === 'drawing') {
            event.detail.blockedReason = 'Salve ou descarte o polígono antes de imprimir.';
        }
    });
    let selectedColor = DEFAULT_COLOR;
    let pendingLayer = null;
    let editingLayer = null;
    let drawHandler = null;
    let workflowStep = 'create';
    let sequence = 1;

    function normalizeColor(color) {
        const value = String(color || '').toUpperCase();
        return Object.prototype.hasOwnProperty.call(ALLOWED_COLORS, value) ? value : DEFAULT_COLOR;
    }

    // v0.3.5: contorno sempre contínuo, inclusive durante o desenho.
    function glebaStyle(color, pending) {
        const normalized = normalizeColor(color);
        return {
            color: normalized,
            weight: pending ? 3.2 : 2.8,
            opacity: 1,
            fillColor: normalized,
            fillOpacity: normalized === '#FFFFFF' ? 0.12 : 0.24
        };
    }

    function glebaDrawOptions(color, pending) {
        return Object.assign({}, glebaStyle(color, pending), {
            pane: 'confrontaGlebasPane',
            renderer: glebasRenderer,
            bubblingMouseEvents: false,
            interactive: true
        });
    }

    function nextId() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
        return `gleba-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function sanitizeName(value, fallback) {
        const text = String(value || '').replace(/\s+/g, ' ').trim().slice(0, 80);
        return text || fallback;
    }

    function nextDefaultName() {
        let value = '';
        do {
            value = `Polígono ${sequence}`;
            sequence += 1;
        } while (findLayerByName(value));
        return value;
    }

    function findLayerByName(name) {
        let found = null;
        drawnItems.eachLayer((layer) => {
            if (layer._confronta && layer._confronta.nome === name) found = layer;
        });
        return found;
    }

    function metadataFor(layer) {
        if (!layer._confronta) {
            layer._confronta = {
                id: nextId(),
                nome: nextDefaultName(),
                cor: selectedColor,
                origem: 'desenhada',
                visivel: true
            };
        }
        layer._confronta.cor = normalizeColor(layer._confronta.cor);
        if (typeof layer._confronta.visivel !== 'boolean') layer._confronta.visivel = true;
        return layer._confronta;
    }

    function featureForLayer(layer) {
        const feature = layer.toGeoJSON();
        const meta = metadataFor(layer);
        feature.properties = Object.assign({}, feature.properties || {}, {
            confronta_id: String(meta.id),
            confronta_nome: meta.nome,
            confronta_cor: meta.cor,
            confronta_origem: meta.origem,
            confronta_visivel: meta.visivel !== false
        });
        return feature;
    }

    function areaHa(feature) {
        try {
            return turf.area(feature) / 10000;
        } catch (error) {
            console.error('CONFRONTA: falha ao calcular área.', error);
            return 0;
        }
    }

    function formatAreaHa(value) {
        return `${Number(value || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ha`;
    }

    function showLiveArea(feature) {
        if (!liveAreaBox || !liveAreaValue) return;
        liveAreaBox.classList.toggle('is-editing-gleba', Boolean(editingLayer));
        const value = feature ? areaHa(feature) : 0;
        const formatted = formatAreaHa(value);
        liveAreaValue.textContent = formatted;
        if (workflowStep === 'drawing' && !editingLayer) {
            // Durante o desenho o HUD é a única leitura de área para não duplicar informação no mapa.
            liveAreaBox.hidden = true;
            if (drawingHudArea) drawingHudArea.textContent = formatted;
        } else {
            liveAreaBox.hidden = false;
        }
    }

    function hideLiveArea() {
        if (!liveAreaBox || !liveAreaValue) return;
        liveAreaBox.hidden = true;
        liveAreaBox.classList.remove('is-editing-gleba');
        liveAreaValue.textContent = '0,00 ha';
        if (drawingHudArea && workflowStep !== 'drawing') drawingHudArea.textContent = 'Marque pelo menos 3 pontos';
    }

    function geometryLooksValid(feature) {
        if (!feature || feature.type !== 'Feature' || !feature.geometry) return false;
        if (!['Polygon', 'MultiPolygon'].includes(feature.geometry.type)) return false;
        try {
            let coordOk = true;
            turf.coordEach(feature, (coordinate) => {
                const lon = Number(coordinate[0]);
                const lat = Number(coordinate[1]);
                if (!Number.isFinite(lon) || !Number.isFinite(lat) || lon < -180 || lon > 180 || lat < -90 || lat > 90) coordOk = false;
            });
            if (!coordOk || areaHa(feature) <= 0) return false;
            if (typeof turf.booleanValid === 'function' && !turf.booleanValid(feature)) return false;
            if (typeof turf.kinks === 'function') {
                const kinks = turf.kinks(feature);
                if (kinks && Array.isArray(kinks.features) && kinks.features.length) return false;
            }
            return true;
        } catch (error) {
            return false;
        }
    }

    const carFeature = consulta.imovel && consulta.imovel.geometry
        ? { type: 'Feature', properties: { car: consulta.imovel.cod_imovel }, geometry: consulta.imovel.geometry }
        : null;

    const referenceFeatures = [];
    function collectReferences(collection, isSicor) {
        Object.values(collection || {}).forEach((layerData) => {
            if (!layerData || !layerData.disponivel || !Array.isArray(layerData.features)) return;
            layerData.features.forEach((feature) => {
                if (!feature || !feature.geometry) return;
                if (isSicor) {
                    const props = feature.properties || {};
                    const identifier = [
                        props.nu_indice_gleba, props.indice_gleba
                    ].map((value) => String(value || '').trim()).find(Boolean);
                    referenceFeatures.push({
                        label: identifier ? `SICOR / Gleba ${identifier}` : 'SICOR / Gleba',
                        feature
                    });
                    return;
                }
                referenceFeatures.push({ label: layerData.label || 'Camada territorial', feature });
            });
        });
    }
    // O aviso operacional da gleba considera somente as camadas internas do SICAR.
    // Bases externas (PRODES, IBAMA, INCRA, CNUC/ICMBio etc.) e a sobreposição
    // com outros CARs continuam disponíveis no mapa, mas não poluem
    // o aviso superior durante desenho, importação ou edição de glebas.
    collectReferences(consulta.camadas);
    const sicorLayerData = consulta.camadas_externas && consulta.camadas_externas.sicor;
    if (sicorLayerData) collectReferences({ sicor: sicorLayerData }, true);

    function intersectionArea(featureA, featureB) {
        try {
            const intersection = turf.intersect(turf.featureCollection([featureA, featureB]));
            return intersection ? turf.area(intersection) : 0;
        } catch (error) {
            try {
                return turf.booleanIntersects(featureA, featureB) ? 0.02 : 0;
            } catch (inner) {
                return 0;
            }
        }
    }

    function isOutsideCar(feature) {
        if (!carFeature || !feature) return false;
        try {
            if (typeof turf.difference === 'function') {
                const diff = turf.difference(turf.featureCollection([feature, carFeature]));
                return Boolean(diff && turf.area(diff) > 0.01);
            }
            return !turf.booleanWithin(feature, carFeature);
        } catch (error) {
            return false;
        }
    }

    function warningLabels(feature, currentLayer) {
        const labels = new Set();
        referenceFeatures.forEach((reference) => {
            if (intersectionArea(feature, reference.feature) > 0.01) labels.add(reference.label);
        });
        if (isOutsideCar(feature)) labels.add('Fora do limite do CAR');
        drawnItems.eachLayer((otherLayer) => {
            if (!otherLayer || otherLayer === currentLayer || typeof otherLayer.toGeoJSON !== 'function') return;
            if (intersectionArea(feature, otherLayer.toGeoJSON()) > 0.01) labels.add(`Polígono: ${metadataFor(otherLayer).nome}`);
        });
        return Array.from(labels);
    }

    function showOverlapAlert(labels, prefix) {
        if (!overlapAlert) return;
        if (!labels || !labels.length) {
            if (serverPropertyAlert) {
                overlapAlert.hidden = false;
                overlapAlert.textContent = serverPropertyAlert;
            } else {
                overlapAlert.hidden = true;
                overlapAlert.textContent = '';
            }
            return;
        }
        overlapAlert.hidden = false;
        overlapAlert.textContent = `${prefix || 'Atenção'}: ${labels.join(', ')}.`;
    }

    function snapshot() {
        const items = drawnItems.getLayers().map((layer) => {
            const feature = featureForLayer(layer);
            const meta = metadataFor(layer);
            const overlaps = warningLabels(feature, layer);
            return {
                id: String(meta.id),
                nome: meta.nome,
                cor: meta.cor,
                origem: meta.origem,
                area_ha: areaHa(feature),
                visivel: meta.visivel !== false,
                alertas: overlaps,
                sobreposicoes: overlaps,
                geometry: feature.geometry
            };
        });
        window.CONFRONTA_GLEBAS_SNAPSHOT = items;
        window.dispatchEvent(new CustomEvent('confronta:glebas-updated', { detail: { items } }));
        return items;
    }

    function buildGlebaPopup(layer) {
        const meta = metadataFor(layer);
        const feature = featureForLayer(layer);
        const alerts = warningLabels(feature, layer);

        const root = document.createElement('section');
        root.className = 'cf-map-popup cf-gleba-popup';

        const head = document.createElement('header');
        head.className = 'cf-gleba-popup-head';
        const dot = document.createElement('span');
        dot.className = 'cf-gleba-popup-dot';
        dot.style.backgroundColor = meta.cor;
        const title = document.createElement('div');
        title.className = 'cf-gleba-popup-title';
        const titleStrong = document.createElement('strong');
        titleStrong.textContent = meta.nome;
        const titleMeta = document.createElement('span');
        titleMeta.textContent = meta.origem === 'consulta_kml' ? 'KML da consulta' : (meta.origem === 'importada' ? 'Polígono importado' : 'Polígono desenhado');
        title.append(titleStrong, titleMeta);
        head.append(dot, title);

        const body = document.createElement('div');
        body.className = 'cf-gleba-popup-body';
        const metrics = document.createElement('div');
        metrics.className = 'cf-gleba-popup-metrics';

        const areaMetric = document.createElement('div');
        areaMetric.className = 'cf-gleba-popup-metric';
        areaMetric.innerHTML = '<span>Área</span>';
        const areaValue = document.createElement('strong');
        areaValue.textContent = formatAreaHa(areaHa(feature));
        areaMetric.appendChild(areaValue);

        const originMetric = document.createElement('div');
        originMetric.className = 'cf-gleba-popup-metric';
        originMetric.innerHTML = '<span>Origem</span>';
        const originValue = document.createElement('strong');
        originValue.textContent = meta.origem === 'consulta_kml' ? 'KML da consulta' : (meta.origem === 'importada' ? 'Importada' : 'Desenhada');
        originMetric.appendChild(originValue);
        metrics.append(areaMetric, originMetric);

        const carRow = document.createElement('div');
        carRow.className = 'cf-gleba-popup-car-line';
        const carLabel = document.createElement('span');
        carLabel.textContent = 'CAR consultado';
        const carValue = document.createElement('strong');
        carValue.textContent = carCode;
        carValue.title = carCode;
        carRow.append(carLabel, carValue);

        const alert = document.createElement('div');
        alert.className = `cf-gleba-popup-alert${alerts.length ? '' : ' is-clear'}`;
        const overlapTitle = document.createElement('strong');
        overlapTitle.textContent = 'Sobreposições';
        alert.appendChild(overlapTitle);
        if (alerts.length) {
            const overlapList = document.createElement('ul');
            alerts.forEach((label) => {
                const item = document.createElement('li');
                item.textContent = label;
                overlapList.appendChild(item);
            });
            alert.appendChild(overlapList);
        } else {
            const none = document.createElement('span');
            none.textContent = 'Nenhuma sobreposição identificada';
            alert.appendChild(none);
        }

        const actions = document.createElement('div');
        actions.className = 'cf-gleba-popup-actions';

        const edit = document.createElement('button');
        edit.type = 'button';
        edit.className = 'cf-gleba-popup-action is-secondary';
        edit.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4Z"></path></svg><span>Editar</span>';

        edit.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            enableLayerEdit(layer);
        });

        const kml = document.createElement('button');
        kml.type = 'button';
        kml.className = 'cf-gleba-popup-action is-primary';
        kml.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12"></path><path d="m7.5 10.5 4.5 4.5 4.5-4.5"></path><path d="M5 20h14"></path></svg><span>Baixar KML</span>';
        kml.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            downloadLayer(layer);
        });

        const csv = document.createElement('button');
        csv.type = 'button';
        csv.className = 'cf-gleba-popup-action is-secondary';
        csv.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h16v16H4z"></path><path d="M4 9h16M9 4v16M15 4v16"></path></svg><span>Baixar CSV</span>';
        csv.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            downloadLayerCsv(layer);
        });

        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'cf-gleba-popup-action is-danger';
        del.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="m19 6-1 14H6L5 6"></path><path d="M10 11v6M14 11v6"></path></svg><span>Excluir</span>';
        del.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            deleteLayer(layer);
        });

        actions.append(edit, kml, csv, del);

        body.append(metrics, carRow, alert, actions);
        root.append(head, body);
        return root;
    }

    function stopGlebaClickPropagation(event) {
        if (event && event.originalEvent) L.DomEvent.stopPropagation(event.originalEvent);
    }

    function bindGlebaPopup(layer) {
        if (!layer || typeof layer.bindPopup !== 'function') return;
        // O pane próprio coloca glebas acima dos CARs e a camada não propaga
        // o clique ao mapa/camadas inferiores, inclusive durante edição.
        layer.options.pane = 'confrontaGlebasPane';
        layer.options.bubblingMouseEvents = false;
        layer.off('click', stopGlebaClickPropagation);
        layer.on('click', stopGlebaClickPropagation);
        try { layer.unbindPopup(); } catch (error) { /* noop */ }
        layer.bindPopup(() => buildGlebaPopup(layer), {
            maxWidth: 330,
            minWidth: 250,
            closeButton: true,
            autoPan: true,
            keepInView: true,
            autoPanPaddingTopLeft: [24, 24],
            autoPanPaddingBottomRight: [24, 92],
            className: 'confronta-gleba-leaflet-popup'
        });
    }

    function refreshGlebaPopups() {
        drawnItems.eachLayer((layer) => {
            bindGlebaPopup(layer);
            if (creditMapActive && metadataFor(layer).visivel !== false) {
                const meta = metadataFor(layer);
                layer.bindTooltip(`${meta.nome} · ${formatAreaHa(areaHa(featureForLayer(layer)))}`, {
                    permanent: true, direction: 'center', className: 'credit-gleba-label', interactive: false, opacity: 1
                });
            } else if (layer.unbindTooltip) {
                layer.unbindTooltip();
            }
        });
    }

    function persistSession() {
        try {
            const features = drawnItems.getLayers().map(featureForLayer);
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ version: STORAGE_VERSION, car: carCode, features }));
        } catch (error) {
            console.warn('CONFRONTA: não foi possível salvar as glebas na sessão.', error);
        }
    }

    function refresh() {
        const layers = drawnItems.getLayers();
        const total = layers.reduce((sum, layer) => sum + areaHa(featureForLayer(layer)), 0);
        if (totalAreaElement) totalAreaElement.textContent = `${total.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ha`;
        if (statusElement) statusElement.textContent = layers.length ? `${layers.length} polígono${layers.length === 1 ? '' : 's'} • áreas desta consulta.` : 'Nenhum polígono desenhado.';
        renderList();
        refreshGlebaPopups();
        persistSession();
        snapshot();
    }

    function setImportStatus(message, error) {
        if (!importStatus) return;
        importStatus.textContent = message || '';
        importStatus.classList.toggle('is-error', Boolean(error));
        importStatus.classList.toggle('is-success', Boolean(message) && !error);
    }

    function showWorkflowStep(step) {
        workflowStep = step;
        if (pendingSavePanel) pendingSavePanel.hidden = step !== 'name';
        if (step === 'name') {
            if (pendingName) window.setTimeout(() => pendingName.focus(), 40);
        }
    }

    function closeWorkflow() {
        if (drawHandler) {
            try { drawHandler.disable(); } catch (error) { /* noop */ }
            drawHandler = null;
        }
        if (pendingLayer) {
            try { map.removeLayer(pendingLayer); } catch (error) { /* noop */ }
            pendingLayer = null;
        }
        if (pendingSavePanel) pendingSavePanel.hidden = true;
        if (pendingName) pendingName.value = '';
        if (pendingArea) pendingArea.textContent = '0,00 ha';
        setImportStatus('', false);
        showOverlapAlert([]);
        hideLiveArea();
        stopDrawingHud();
        workflowStep = 'create';
    }

    function setSelectedColor(color) {
        selectedColor = normalizeColor(color);
        if (colorPicker) {
            colorPicker.querySelectorAll('.gleba-color-swatch').forEach((button) => {
                button.classList.toggle('is-selected', normalizeColor(button.dataset.color) === selectedColor);
            });
        }
        if (drawingHudColor) {
            drawingHudColor.style.backgroundColor = selectedColor;
            drawingHudColor.title = `Cor: ${ALLOWED_COLORS[selectedColor]} — clique para trocar`;
            drawingHudColor.setAttribute('aria-label', `Cor do polígono: ${ALLOWED_COLORS[selectedColor]}. Alterar cor`);
        }
        map.getContainer().style.setProperty('--cf-gleba-vertex', selectedColor);
        if (drawHandler && workflowStep === 'drawing') {
            try { drawHandler.setOptions({ shapeOptions: glebaDrawOptions(selectedColor, true) }); } catch (error) { /* noop */ }
            if (drawHandler._poly && typeof drawHandler._poly.setStyle === 'function') drawHandler._poly.setStyle(glebaStyle(selectedColor, true));
        }
    }

    function featureFromVertexLayerGroup(layerGroup) {
        if (!layerGroup || typeof layerGroup.eachLayer !== 'function') return null;
        const coordinates = [];
        layerGroup.eachLayer((marker) => {
            if (!marker || typeof marker.getLatLng !== 'function') return;
            const latlng = marker.getLatLng();
            coordinates.push([latlng.lng, latlng.lat]);
        });
        if (coordinates.length < 3) return null;
        coordinates.push(coordinates[0].slice());
        try { return turf.polygon([coordinates]); } catch (error) { return null; }
    }

    function drawVertexCount() {
        return drawHandler && Array.isArray(drawHandler._markers) ? drawHandler._markers.length : 0;
    }

    function highlightFirstVertex() {
        if (!drawHandler || !Array.isArray(drawHandler._markers)) return;
        drawHandler._markers.forEach((marker, index) => {
            if (!marker || !marker._icon) return;
            marker._icon.classList.toggle('cf-first-vertex', index === 0 && drawHandler._markers.length >= 3);
            if (index === 0) marker._icon.title = drawHandler._markers.length >= 3 ? 'Primeiro ponto — clique para concluir' : 'Primeiro ponto';
        });
    }

    function updateDrawingControls() {
        const count = drawVertexCount();
        if (drawUndo) drawUndo.disabled = count === 0;
        if (drawFinish) drawFinish.disabled = count < 3;
        if (drawingHudArea && count < 3) drawingHudArea.textContent = count ? `${count} ponto${count > 1 ? 's' : ''} — marque pelo menos 3` : 'Marque pelo menos 3 pontos';
        highlightFirstVertex();
    }

    function stopDrawingHud() {
        if (drawingHud) drawingHud.hidden = true;
        document.body.classList.remove('is-drawing-gleba');
    }

    function startDrawing() {
        if (window.CONFRONTA_MEASURE_ACTIVE && typeof window.CONFRONTA_STOP_MEASURE_DISTANCE === 'function') {
            window.CONFRONTA_STOP_MEASURE_DISTANCE();
        }
        if (drawHandler) drawHandler.disable();
        showWorkflowStep('drawing');
        setSelectedColor(selectedColor);
        drawHandler = new L.Draw.Polygon(map, {
            allowIntersection: false,
            showArea: false,
            repeatMode: false,
            shapeOptions: glebaDrawOptions(selectedColor, true)
        });
        drawHandler.enable();
        if (drawingHud) drawingHud.hidden = false;
        document.body.classList.add('is-drawing-gleba');
        showLiveArea(null);
        updateDrawingControls();
    }

    function savePendingLayer() {
        if (!pendingLayer) return;
        const feature = pendingLayer.toGeoJSON();
        if (!geometryLooksValid(feature)) {
            window.alert('A geometria do polígono não é válida.');
            return;
        }
        const name = sanitizeName(pendingName && pendingName.value, nextDefaultName());
        pendingLayer._confronta = {
            id: nextId(),
            nome: name,
            cor: selectedColor,
            origem: 'desenhada',
            visivel: true
        };
        if (typeof pendingLayer.setStyle === 'function') pendingLayer.setStyle(glebaStyle(selectedColor, false));
        map.removeLayer(pendingLayer);
        pendingLayer.options.pane = 'confrontaGlebasPane';
        pendingLayer.options.renderer = glebasRenderer;
        pendingLayer.options.bubblingMouseEvents = false;
        drawnItems.addLayer(pendingLayer);
        const saved = pendingLayer;
        pendingLayer = null;
        refresh();
        const savedWarnings = warningLabels(featureForLayer(saved), saved);
        closeWorkflow();
        showOverlapAlert(savedWarnings, `Atenção — ${name}`);
    }

    function restoreLayerGeometry(layer, feature) {
        if (!layer || !feature || !feature.geometry || typeof layer.setLatLngs !== 'function') return false;
        try {
            const temporary = L.geoJSON(feature, {
                pane: 'confrontaGlebasPane', renderer: glebasRenderer, bubblingMouseEvents: false
            });
            const source = temporary.getLayers()[0];
            if (!source || typeof source.getLatLngs !== 'function') return false;
            layer.setLatLngs(source.getLatLngs());
            if (typeof layer.redraw === 'function') layer.redraw();
            return true;
        } catch (error) {
            console.warn('CONFRONTA: não foi possível restaurar a geometria anterior da gleba.', error);
            return false;
        }
    }

    function removeEditVertexListeners(layer) {
        (layer._confrontaEditDragMarkers || []).forEach((marker) => marker.off('drag', updateEditingLayer));
        layer._confrontaEditDragMarkers = [];
    }

    function addEditVertexListeners(layer) {
        removeEditVertexListeners(layer);
        const handlers = layer.editing && layer.editing._verticesHandlers;
        const markers = [];
        (handlers || []).forEach((handler) => {
            (handler._markers || []).forEach((marker) => {
                marker.on('drag', updateEditingLayer);
                markers.push(marker);
            });
        });
        layer._confrontaEditDragMarkers = markers;
    }

    function clearLayerEdit(layer) {
        removeEditVertexListeners(layer);
        layer.off('edit editdrag', updateEditingLayer);
        if (layer.editing && layer.editing.enabled()) layer.editing.disable();
        if (editingLayer === layer) editingLayer = null;
        layer._confrontaEditSnapshot = null;
        if (editControls) editControls.hidden = true;
        if (liveAreaBox) liveAreaBox.classList.remove('is-editing-gleba');
        hideLiveArea();
    }

    function cancelLayerEdit() {
        const layer = editingLayer;
        if (!layer) return;
        const original = layer._confrontaEditSnapshot;
        clearLayerEdit(layer);
        if (original) restoreLayerGeometry(layer, original);
        persistSession();
        refresh();
        showOverlapAlert(warningLabels(featureForLayer(layer), layer), `Edição cancelada — ${metadataFor(layer).nome}`);
    }

    function saveLayerEdit() {
        const layer = editingLayer;
        if (!layer) return;
        if (!geometryLooksValid(layer.toGeoJSON())) {
            window.alert('A geometria do polígono não é válida. Ajuste os vértices antes de salvar.');
            return;
        }
        const meta = metadataFor(layer);
        clearLayerEdit(layer);
        refresh();
        showOverlapAlert(warningLabels(featureForLayer(layer), layer), `Atenção — ${meta.nome}`);
    }

    function updateEditingLayer() {
        const layer = editingLayer;
        if (!layer) return;
        const feature = layer.toGeoJSON();
        showLiveArea(feature);
        if (layer._confrontaThumbnailPath) {
            layer._confrontaThumbnailPath.setAttribute('d', polygonThumbnailPath(feature.geometry));
        }
        showOverlapAlert(warningLabels(feature, layer), 'Atenção — edição');
    }

    function enableLayerEdit(layer) {
        if (!layer || !layer.editing || metadataFor(layer).visivel === false) return;
        if (editingLayer === layer) return;
        if (editingLayer) cancelLayerEdit();
        const meta = metadataFor(layer);
        editingLayer = layer;
        layer._confrontaEditSnapshot = JSON.parse(JSON.stringify(layer.toGeoJSON()));
        map.getContainer().style.setProperty('--cf-gleba-vertex', meta.cor);
        layer.editing.enable();
        layer.off('edit editdrag', updateEditingLayer);
        layer.on('edit editdrag', updateEditingLayer);
        addEditVertexListeners(layer);
        if (editControls) editControls.hidden = false;
        showLiveArea(layer.toGeoJSON());
        map.closePopup();
        renderList();
    }

    function applyLayerVisibility(layer) {
        if (!layer) return;
        const meta = metadataFor(layer);
        const visible = meta.visivel !== false;
        if (typeof layer.setStyle === 'function') {
            const style = glebaStyle(meta.cor, false);
            if (!visible) {
                style.opacity = 0;
                style.fillOpacity = 0;
            }
            layer.setStyle(style);
        }
        const element = typeof layer.getElement === 'function' ? layer.getElement() : null;
        if (element) element.style.pointerEvents = visible ? '' : 'none';
        if (!visible && layer.editing && layer.editing.enabled()) {
            if (editingLayer === layer) cancelLayerEdit();
            else {
                try { layer.editing.disable(); } catch (error) { /* noop */ }
            }
        }
    }

    function toggleLayerVisibility(layer) {
        const meta = metadataFor(layer);
        meta.visivel = meta.visivel === false;
        applyLayerVisibility(layer);
        if (creditMapActive) refreshGlebaPopups();
        persistSession();
        snapshot();
        renderList();
    }

    function deleteLayer(layer) {
        const meta = metadataFor(layer);
        if (!window.confirm(`Excluir o polígono "${meta.nome}" desta sessão?`)) return;
        if (editingLayer === layer) cancelLayerEdit();
        drawnItems.removeLayer(layer);
        refresh();
        showOverlapAlert([]);
        map.closePopup();
    }

    function polygonThumbnailPath(geometry) {
        const polygons = geometry?.type === 'Polygon'
            ? [geometry.coordinates]
            : (geometry?.type === 'MultiPolygon' ? geometry.coordinates : []);
        const rings = polygons.flatMap((polygon) => Array.isArray(polygon) ? polygon : [])
            .map((ring) => ring.filter((point) => Array.isArray(point)
                && Number.isFinite(Number(point[0])) && Number.isFinite(Number(point[1])))
                .map((point) => [Number(point[0]), Number(point[1])]))
            .filter((ring) => ring.length >= 3);
        const points = rings.flat();
        if (!points.length) return '';

        const meanLatitude = points.reduce((sum, point) => sum + point[1], 0) / points.length;
        const longitudeScale = Math.max(0.05, Math.cos(meanLatitude * Math.PI / 180));
        const projected = points.map(([longitude, latitude]) => [longitude * longitudeScale, latitude]);
        const bounds = projected.reduce((result, [x, y]) => ({
            minX: Math.min(result.minX, x), maxX: Math.max(result.maxX, x),
            minY: Math.min(result.minY, y), maxY: Math.max(result.maxY, y)
        }), { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity });
        const { minX, maxX, minY, maxY } = bounds;
        const width = Math.max(maxX - minX, 1e-9);
        const height = Math.max(maxY - minY, 1e-9);
        const scale = Math.min(40 / width, 26 / height);
        const offsetX = 24 - ((minX + maxX) / 2) * scale;
        const offsetY = 18 + ((minY + maxY) / 2) * scale;

        return rings.map((ring) => {
            const coords = ring.map(([longitude, latitude]) => {
                const x = longitude * longitudeScale * scale + offsetX;
                const y = offsetY - latitude * scale;
                return [x.toFixed(2), y.toFixed(2)];
            });
            if (coords.length > 1 && coords[0][0] === coords.at(-1)[0] && coords[0][1] === coords.at(-1)[1]) coords.pop();
            return coords.map(([x, y], index) => `${index ? 'L' : 'M'}${x} ${y}`).join(' ') + ' Z';
        }).join(' ');
    }

    function updatePolygonThumbnail(path, geometry, color) {
        path.setAttribute('d', polygonThumbnailPath(geometry));
        path.setAttribute('fill', color || '#2DD4BF');
        path.setAttribute('stroke', color || '#2DD4BF');
    }

    function renderList() {
        if (!listElement) return;
        listElement.replaceChildren();

        drawnItems.getLayers().forEach((layer) => {
            const meta = metadataFor(layer);
            const feature = featureForLayer(layer);
            const visible = meta.visivel !== false;

            const item = document.createElement('article');
            const isEditing = editingLayer === layer;
            item.className = `gleba-item${visible ? '' : ' is-hidden'}${isEditing ? ' is-editing' : ''}`;

            const locateGleba = () => {
                const bounds = layer.getBounds && layer.getBounds();
                if (!bounds || !bounds.isValid()) return;
                map.fitBounds(bounds, {
                    padding: [36, 36],
                    maxZoom: context.maxNativeZoom || context.maxZoom || 17,
                    animate: false
                });
                if (layer.getPopup && layer.getPopup()) layer.openPopup();
            };

            const eye = document.createElement('button');
            eye.type = 'button';
            eye.className = `gleba-item-eye${visible ? ' is-active' : ''}`;
            eye.title = visible ? 'Ocultar polígono no mapa' : 'Mostrar polígono no mapa';
            eye.setAttribute('aria-label', eye.title);
            eye.setAttribute('aria-pressed', visible ? 'true' : 'false');
            eye.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.7"/><path class="gleba-eye-off-mark" d="M4 4l16 16"/></svg>';
            eye.addEventListener('click', () => toggleLayerVisibility(layer));

            const visual = document.createElement('div');
            visual.className = 'gleba-item-visual';

            const main = document.createElement('div');
            main.className = 'gleba-item-main';

            const heading = document.createElement('div');
            heading.className = 'gleba-item-heading';
            const geometryIcon = document.createElement('span');
            geometryIcon.className = 'gleba-item-geometry';
            geometryIcon.setAttribute('aria-hidden', 'true');
            const thumbnail = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            thumbnail.setAttribute('viewBox', '0 0 48 36');
            thumbnail.setAttribute('aria-hidden', 'true');
            const thumbnailPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            thumbnailPath.setAttribute('fill-opacity', '0.24');
            thumbnailPath.setAttribute('fill-rule', 'evenodd');
            thumbnailPath.setAttribute('stroke-width', '1.6');
            thumbnailPath.setAttribute('stroke-linejoin', 'round');
            thumbnailPath.setAttribute('vector-effect', 'non-scaling-stroke');
            updatePolygonThumbnail(thumbnailPath, feature.geometry, meta.cor);
            thumbnail.appendChild(thumbnailPath);
            geometryIcon.appendChild(thumbnail);
            layer._confrontaThumbnailPath = thumbnailPath;
            visual.append(eye, geometryIcon);

            const name = document.createElement('button');
            name.type = 'button';
            name.textContent = meta.nome;
            name.className = 'gleba-name-trigger';
            name.setAttribute('aria-label', `Localizar ${meta.nome} no mapa`);
            name.title = `Localizar ${meta.nome} no mapa`;
            name.addEventListener('click', locateGleba);
            heading.append(name);

            const metaRow = document.createElement('div');
            metaRow.className = 'gleba-item-meta';
            const area = document.createElement('strong');
            area.textContent = formatAreaHa(areaHa(feature));
            metaRow.append(area);

            const actions = document.createElement('div');
            actions.className = 'gleba-item-actions';
            const edit = document.createElement('button');
            edit.type = 'button';
            edit.className = 'gleba-item-action';
            edit.textContent = 'Editar';
            edit.disabled = !visible;
            edit.addEventListener('click', () => enableLayerEdit(layer));
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'gleba-item-action is-danger';
            remove.textContent = 'Excluir';
            remove.addEventListener('click', () => deleteLayer(layer));
            if (isEditing) {
                const save = document.createElement('button');
                save.type = 'button';
                save.className = 'gleba-item-action is-save';
                save.textContent = 'Salvar';
                save.setAttribute('aria-label', `Salvar alterações de ${meta.nome}`);
                save.addEventListener('click', saveLayerEdit);
                actions.append(edit, remove, save);
            } else {
                actions.append(edit, remove);
            }

            main.append(heading, metaRow, actions);
            item.append(visual, main);
            listElement.appendChild(item);
            applyLayerVisibility(layer);
        });
    }

    function carregarGlebasTemporariasDaConsulta() {
        const element = document.getElementById('glebas-temporarias-data');
        if (!element) return 0;

        let payload = null;

        try {
            payload = JSON.parse(element.textContent || 'null');
        } catch (error) {
            console.warn(
                'CONFRONTA: glebas temporárias inválidas.',
                error
            );
            return 0;
        }

        if (
            !payload
            || !Array.isArray(payload.items)
            || !payload.items.length
        ) {
            return 0;
        }

        if (payload.replace === true) {
            drawnItems.clearLayers();
            try {
                sessionStorage.removeItem(STORAGE_KEY);
            } catch (error) {
                /* noop */
            }
        }

        const colors = [
            '#2563EB',
            '#0891B2',
            '#EAB308',
            '#F97316',
            '#DC2626',
            '#A855F7'
        ];

        let quantidade = 0;

        payload.items.forEach((feature, index) => {
            if (!feature || !feature.geometry) return;

            const nome = feature.properties
                && (
                    feature.properties.confronta_nome
                    || feature.properties.name
                );

            const added = addImportedFeature(
                feature,
                {
                    nome: nome || `Polígono ${index + 1}`,
                    cor: colors[index % colors.length],
                    origem: 'consulta_kml',
                    visivel: true
                },
                false
            );

            quantidade += added.length;
        });

        return quantidade;
    }


    function restoreSession() {
        try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return;
            const parsed = JSON.parse(raw);
            if (!parsed || parsed.version !== STORAGE_VERSION || parsed.car !== carCode || !Array.isArray(parsed.features)) return;
            parsed.features.forEach((feature) => addImportedFeature(feature, {
                nome: feature.properties && feature.properties.confronta_nome,
                cor: feature.properties && feature.properties.confronta_cor,
                origem: feature.properties && feature.properties.confronta_origem,
                visivel: !(feature.properties && feature.properties.confronta_visivel === false),
                id: feature.properties && feature.properties.confronta_id
            }, false));
        } catch (error) {
            console.warn('CONFRONTA: não foi possível restaurar as glebas da sessão.', error);
        }
    }

    function addImportedFeature(feature, options, doRefresh) {
        if (!geometryLooksValid(feature)) throw new Error('A geometria importada não é válida.');
        const opts = options || {};
        const color = normalizeColor(opts.cor || selectedColor);
        const baseName = sanitizeName(opts.nome || (feature.properties && (feature.properties.confronta_nome || feature.properties.name)), nextDefaultName());
        const temporary = L.geoJSON(feature, {
            pane: 'confrontaGlebasPane',
            renderer: glebasRenderer,
            bubblingMouseEvents: false,
            style: glebaStyle(color, false)
        });
        const added = [];
        let part = 0;
        temporary.eachLayer((layer) => {
            layer._confronta = {
                id: part === 0 ? String(opts.id || nextId()) : nextId(),
                nome: part === 0 ? baseName : `${baseName} ${part + 1}`,
                cor: color,
                origem: opts.origem || 'importada',
                visivel: opts.visivel !== false
            };
            if (typeof layer.setStyle === 'function') layer.setStyle(glebaStyle(color, false));
            drawnItems.addLayer(layer);
            applyLayerVisibility(layer);
            added.push(layer);
            part += 1;
        });
        if (doRefresh !== false) refresh();
        return added;
    }

    // ---------- KML ----------
    function xmlEscape(value) {
        return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');
    }
    function lineCoordinates(coordinates) {
        return (coordinates || []).map((coordinate) => `${Number(coordinate[0])},${Number(coordinate[1])},${Number(coordinate[2]) || 0}`).join(' ');
    }
    function geometryToKml(geometry) {
        if (!geometry || !geometry.coordinates) return '';
        if (geometry.type === 'Polygon') {
            const rings = geometry.coordinates.map((ring, index) => {
                const type = index === 0 ? 'outerBoundaryIs' : 'innerBoundaryIs';
                return `<${type}><LinearRing><tessellate>1</tessellate><coordinates>${lineCoordinates(ring)}</coordinates></LinearRing></${type}>`;
            }).join('');
            return `<Polygon><tessellate>1</tessellate>${rings}</Polygon>`;
        }
        if (geometry.type === 'MultiPolygon') {
            return `<MultiGeometry>${geometry.coordinates.map((coords) => geometryToKml({ type: 'Polygon', coordinates: coords })).join('')}</MultiGeometry>`;
        }
        return '';
    }
    function hexToKml(color, alpha) {
        const value = normalizeColor(color).replace('#', '').toUpperCase();
        return `${alpha || 'ff'}${value.substring(4, 6)}${value.substring(2, 4)}${value.substring(0, 2)}`.toLowerCase();
    }
    function safeId(value) {
        return String(value || nextId()).replace(/[^A-Za-z0-9_-]/g, '') || `gleba-${Date.now()}`;
    }
    function safeFilename(value) {
        return String(value || 'gleba').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^_+|_+$/g, '') || 'gleba';
    }
    function placemarkKml(layer) {
        const feature = featureForLayer(layer);
        const meta = metadataFor(layer);
        const alerts = warningLabels(feature, layer);
        const styleId = `style-${safeId(meta.id)}`;
        return `<Style id="${xmlEscape(styleId)}"><LineStyle><color>${hexToKml(meta.cor, 'ff')}</color><width>3</width></LineStyle><PolyStyle><color>${hexToKml(meta.cor, '35')}</color></PolyStyle></Style><Placemark><name>${xmlEscape(meta.nome)}</name><styleUrl>#${xmlEscape(styleId)}</styleUrl><ExtendedData><Data name="CAR"><value>${xmlEscape(carCode)}</value></Data><Data name="AREA_HA"><value>${areaHa(feature).toFixed(4)}</value></Data><Data name="ORIGEM"><value>${xmlEscape(meta.origem)}</value></Data><Data name="ALERTAS"><value>${xmlEscape(alerts.join(', '))}</value></Data></ExtendedData>${geometryToKml(feature.geometry)}</Placemark>`;
    }
    function kmlDocument(layers, name) {
        return `<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>${xmlEscape(name)}</name>${layers.map(placemarkKml).join('')}</Document></kml>`;
    }
    function downloadText(filename, content) {
        const blob = new Blob(['\uFEFF', content], { type: 'application/vnd.google-earth.kml+xml;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        anchor.style.display = 'none';
        document.body.appendChild(anchor);
        anchor.click();
        window.setTimeout(() => {
            URL.revokeObjectURL(url);
            if (anchor.parentNode) anchor.parentNode.removeChild(anchor);
        }, 5000);
    }
    function downloadLayer(layer) {
        try {
            const meta = metadataFor(layer);
            downloadText(`${safeFilename(meta.nome)}_${safeFilename(carCode)}.kml`, kmlDocument([layer], `${meta.nome} - ${carCode}`));
        } catch (error) {
            console.error(error);
            window.alert('Não foi possível baixar este polígono.');
        }
    }
    function csvEscape(value) {
        const text = String(value ?? '');
        return `"${text.replace(/"/g, '""')}"`;
    }

    function csvNumber(value, digits) {
        const number = Number(value);
        if (!Number.isFinite(number)) return '';
        return number.toFixed(digits).replace('.', ',');
    }

    function rowsForCsv(layer) {
        const feature = featureForLayer(layer);
        const meta = metadataFor(layer);
        const rows = [];
        const geometry = feature.geometry || {};
        const polygons = geometry.type === 'Polygon'
            ? [geometry.coordinates]
            : (geometry.type === 'MultiPolygon' ? geometry.coordinates : []);

        polygons.forEach((polygon, polygonIndex) => {
            polygon.forEach((ring, ringIndex) => {
                (ring || []).forEach((coordinate, vertexIndex) => {
                    rows.push([
                        meta.nome, carCode,
                        meta.origem === 'importada' ? 'Importado' : 'Desenhado',
                        areaHa(feature),
                        polygonIndex + 1, ringIndex + 1, vertexIndex + 1,
                        Number(coordinate[0]), Number(coordinate[1])
                    ]);
                });
            });
        });
        return rows;
    }

    function csvDocument(layers) {
        const header = ['nome', 'car', 'origem', 'area_ha', 'poligono', 'anel', 'vertice', 'longitude', 'latitude'];
        const rows = [];
        (layers || []).forEach((layer) => rowsForCsv(layer).forEach((row) => rows.push(row)));
        return [
            header.map(csvEscape).join(';'),
            ...rows.map((row) => row.map((value, index) => {
                if (index === 3) return csvEscape(csvNumber(value, 4));
                if (index === 7 || index === 8) return csvEscape(csvNumber(value, 8));
                return csvEscape(value);
            }).join(';'))
        ].join('\r\n');
    }

    function downloadCsvText(filename, content) {
        const blob = new Blob(['\uFEFF', content], { type: 'text/csv;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        anchor.style.display = 'none';
        document.body.appendChild(anchor);
        anchor.click();
        window.setTimeout(() => {
            URL.revokeObjectURL(url);
            if (anchor.parentNode) anchor.parentNode.removeChild(anchor);
        }, 5000);
    }

    function downloadLayerCsv(layer) {
        const meta = metadataFor(layer);
        downloadCsvText(`${safeFilename(meta.nome)}_${safeFilename(carCode)}.csv`, csvDocument([layer]));
    }

    // ---------- Importação ----------
    function parseCoordinatesText(text) {
        const raw = String(text || '').trim().split(/\s+/).map((tuple) => {
            const parts = tuple.split(',').map(Number);
            return [parts[0], parts[1]];
        }).filter((coord) => Number.isFinite(coord[0]) && Number.isFinite(coord[1]));

        const coordinates = [];

        raw.forEach((coord) => {
            const previous = coordinates[coordinates.length - 1];

            // Remove vértices consecutivos duplicados.
            if (
                !previous
                || previous[0] !== coord[0]
                || previous[1] !== coord[1]
            ) {
                coordinates.push(coord);
            }
        });

        if (coordinates.length < 3) {
            throw new Error('Polígono KML inválido.');
        }

        // Mantém exatamente um ponto de fechamento.
        while (
            coordinates.length > 1
            && coordinates[coordinates.length - 1][0] === coordinates[coordinates.length - 2][0]
            && coordinates[coordinates.length - 1][1] === coordinates[coordinates.length - 2][1]
        ) {
            coordinates.pop();
        }

        const first = coordinates[0];
        const last = coordinates[coordinates.length - 1];

        if (first[0] !== last[0] || first[1] !== last[1]) {
            coordinates.push(first.slice());
        }

        return coordinates;
    }
    function firstCoordinates(boundaryNode) {
        if (!boundaryNode) return null;
        const node = boundaryNode.getElementsByTagName('coordinates')[0];
        return node ? parseCoordinatesText(node.textContent) : null;
    }
    function parseKml(text, fallbackName) {
        const xml = new DOMParser().parseFromString(text, 'application/xml');
        if (xml.getElementsByTagName('parsererror').length) throw new Error('KML inválido.');
        const result = [];
        const placemarks = Array.from(xml.getElementsByTagName('Placemark'));
        const targets = placemarks.length ? placemarks : [xml.documentElement];
        targets.forEach((placemark, pIndex) => {
            const nameNode = placemark.getElementsByTagName('name')[0];
            const name = sanitizeName(nameNode && nameNode.textContent, `${fallbackName} ${pIndex + 1}`);
            Array.from(placemark.getElementsByTagName('Polygon')).forEach((polygonNode, polygonIndex) => {
                const outer = firstCoordinates(polygonNode.getElementsByTagName('outerBoundaryIs')[0]);
                if (!outer) return;
                const rings = [outer];
                Array.from(polygonNode.getElementsByTagName('innerBoundaryIs')).forEach((inner) => {
                    const ring = firstCoordinates(inner);
                    if (ring) rings.push(ring);
                });
                result.push({ feature: turf.polygon(rings), nome: polygonIndex ? `${name} ${polygonIndex + 1}` : name });
            });
        });
        if (!result.length) throw new Error('O KML não contém polígonos.');
        return result;
    }
    function splitGeoJsonFeature(feature, fallbackName, index) {
        if (!feature || !feature.geometry) return [];
        const name = sanitizeName(feature.properties && (feature.properties.confronta_nome || feature.properties.name), `${fallbackName} ${index + 1}`);
        if (feature.geometry.type === 'Polygon') return [{ feature, nome: name }];
        if (feature.geometry.type === 'MultiPolygon') return feature.geometry.coordinates.map((coords, part) => ({ feature: turf.polygon(coords), nome: `${name} ${part + 1}` }));
        return [];
    }
    function parseGeoJson(text, fallbackName) {
        let parsed;
        try { parsed = JSON.parse(text); } catch (error) { throw new Error('GeoJSON inválido.'); }
        let features = [];
        if (parsed.type === 'FeatureCollection') features = parsed.features || [];
        else if (parsed.type === 'Feature') features = [parsed];
        else if (['Polygon', 'MultiPolygon'].includes(parsed.type)) features = [{ type: 'Feature', properties: {}, geometry: parsed }];
        const result = [];
        features.forEach((feature, index) => splitGeoJsonFeature(feature, fallbackName, index).forEach((item) => result.push(item)));
        if (!result.length) throw new Error('O arquivo não contém Polygon ou MultiPolygon.');
        return result;
    }
    async function importFile(file) {
        if (!file) return;
        if (file.size > MAX_IMPORT_BYTES) throw new Error('O arquivo excede 5 MB.');
        const text = await file.text();
        const extension = (file.name.split('.').pop() || '').toLowerCase();
            const fallbackName = sanitizeName(file.name.replace(/\.[^.]+$/, ''), 'Polígono importado');
        const items = extension === 'kml' ? parseKml(text, fallbackName) : parseGeoJson(text, fallbackName);
        if (items.length > MAX_IMPORT_POLYGONS) throw new Error(`O arquivo possui mais de ${MAX_IMPORT_POLYGONS} polígonos.`);
        const added = [];
        items.forEach((item) => addImportedFeature(item.feature, { nome: item.nome, cor: selectedColor, origem: 'importada' }).forEach((layer) => added.push(layer)));
        if (!added.length) throw new Error('Nenhuma gleba válida foi importada.');
        const bounds = L.featureGroup(added).getBounds();
        if (bounds.isValid()) map.fitBounds(bounds, { padding: [30, 30], maxZoom: context.maxNativeZoom || 18, animate: false });
        const warningSet = new Set();
        added.forEach((layer) => warningLabels(featureForLayer(layer), layer).forEach((label) => warningSet.add(label)));
        refresh();
        closeWorkflow();
        showOverlapAlert(Array.from(warningSet), 'Polígono importado — atenção');
    }

    // ---------- Eventos de interface ----------
    if (startButton) startButton.addEventListener('click', startDrawing);
    if (importButton && importInput) importButton.addEventListener('click', () => {
        importInput.value = '';
        importInput.click();
    });

    if (colorPicker) colorPicker.querySelectorAll('.gleba-color-swatch').forEach((button) => {
        button.addEventListener('click', () => setSelectedColor(button.dataset.color));
    });

    async function handleImportFile(file) {
        if (!file) return;
        setImportStatus('Importando e validando a geometria...', false);
        try {
            await importFile(file);
        } catch (error) {
            console.error(error);
            setImportStatus(error.message || 'Não foi possível importar o polígono.', true);
        } finally {
            if (importInput) importInput.value = '';
        }
    }

    if (importInput) importInput.addEventListener('change', () => {
        const file = importInput.files && importInput.files[0];
        handleImportFile(file);
    });

    if (pendingSave) pendingSave.addEventListener('click', savePendingLayer);
    if (pendingDiscard) pendingDiscard.addEventListener('click', closeWorkflow);
    if (drawingHudColor) drawingHudColor.addEventListener('click', () => {
        const colors = Object.keys(ALLOWED_COLORS);
        const currentIndex = Math.max(0, colors.indexOf(selectedColor));
        setSelectedColor(colors[(currentIndex + 1) % colors.length]);
        drawingHudColor.title = `Cor: ${ALLOWED_COLORS[selectedColor]} — clique para trocar`;
        drawingHudColor.setAttribute('aria-label', `Cor da gleba: ${ALLOWED_COLORS[selectedColor]}. Alterar cor`);
    });
    if (drawUndo) drawUndo.addEventListener('click', () => {
        if (!drawHandler || typeof drawHandler.deleteLastVertex !== 'function') return;
        drawHandler.deleteLastVertex();
        updateDrawingControls();
    });
    if (drawFinish) drawFinish.addEventListener('click', () => {
        if (!drawHandler || drawVertexCount() < 3 || typeof drawHandler.completeShape !== 'function') return;
        drawHandler.completeShape();
    });
    if (drawCancel) drawCancel.addEventListener('click', closeWorkflow);
    if (editSaveButton) editSaveButton.addEventListener('click', saveLayerEdit);
    if (editCancelButton) editCancelButton.addEventListener('click', cancelLayerEdit);

    map.on('draw:drawvertex', (event) => {
        updateDrawingControls();
        const feature = featureFromVertexLayerGroup(event.layers);
        if (feature) {
            showLiveArea(feature);
            showOverlapAlert(warningLabels(feature, null), 'Atenção — desenho');
        } else {
            showLiveArea(null);
        }
    });

    map.on('draw:editvertex', updateEditingLayer);

    map.on(L.Draw.Event.CREATED, (event) => {
        if (window.CONFRONTA_QUERY_DRAW_ACTIVE) return;
        if (workflowStep !== 'drawing') return;
        if (drawHandler) {
            try { drawHandler.disable(); } catch (error) { /* noop */ }
            drawHandler = null;
        }
        stopDrawingHud();
        const feature = event.layer.toGeoJSON();
        if (!geometryLooksValid(feature)) {
            window.alert('O polígono desenhado é inválido. Tente novamente.');
            showWorkflowStep('create');
            return;
        }
        pendingLayer = event.layer;
        pendingLayer.options.pane = 'confrontaGlebasPane';
        pendingLayer.options.renderer = glebasRenderer;
        pendingLayer.options.bubblingMouseEvents = false;
        if (typeof pendingLayer.setStyle === 'function') pendingLayer.setStyle(glebaStyle(selectedColor, true));
        pendingLayer.addTo(map);
        if (pendingArea) pendingArea.textContent = formatAreaHa(areaHa(feature));
        hideLiveArea();
        if (pendingName) pendingName.value = '';
        showOverlapAlert(warningLabels(feature, null), 'Atenção — novo polígono');
        showWorkflowStep('name');
    });

    setSelectedColor(DEFAULT_COLOR);
    restoreSession();
    carregarGlebasTemporariasDaConsulta();
    refresh();
})();
