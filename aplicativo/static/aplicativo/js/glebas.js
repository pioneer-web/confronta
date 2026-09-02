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

    const listView = document.getElementById('gleba-list-view');
    const workflow = document.getElementById('gleba-workflow');
    const workflowTitle = document.getElementById('gleba-workflow-title');
    const workflowBack = document.getElementById('gleba-workflow-back');
    const workflowCancel = document.getElementById('gleba-workflow-cancel');
    const startButton = document.getElementById('start-new-gleba');
    const colorPicker = document.getElementById('gleba-color-picker');
    const methodDraw = document.getElementById('gleba-method-draw');
    const methodImport = document.getElementById('gleba-method-import');
    const importInput = document.getElementById('import-gleba-file');
    const importStatus = document.getElementById('gleba-import-status');
    const pendingName = document.getElementById('gleba-pending-name');
    const pendingArea = document.getElementById('gleba-pending-area');
    const pendingSave = document.getElementById('gleba-pending-save');
    const pendingDiscard = document.getElementById('gleba-pending-discard');
    const listElement = document.getElementById('gleba-list');
    const totalAreaElement = document.getElementById('gleba-area');
    const statusElement = document.getElementById('gleba-status');
    const downloadAllButton = document.getElementById('download-drawn-kml');
    const overlapAlert = document.getElementById('overlap-alert');
    const liveAreaBox = document.getElementById('gleba-live-area-map');
    const liveAreaValue = document.getElementById('gleba-live-area-value');
    const drawingHud = document.getElementById('gleba-drawing-hud');
    const drawingHudArea = document.getElementById('gleba-hud-area');
    const drawingHudColor = document.getElementById('gleba-hud-color');
    const drawUndo = document.getElementById('gleba-draw-undo');
    const drawFinish = document.getElementById('gleba-draw-finish');
    const drawCancel = document.getElementById('gleba-draw-cancel');

    const stepCreate = document.getElementById('gleba-step-create');
    const stepDrawing = document.getElementById('gleba-step-drawing');
    const stepName = document.getElementById('gleba-step-name');

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

    const drawnItems = new L.FeatureGroup().addTo(map);
    let selectedColor = DEFAULT_COLOR;
    let pendingLayer = null;
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
            fillOpacity: normalized === '#FFFFFF' ? 0.08 : 0.14
        };
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
            value = `Gleba ${sequence}`;
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
                origem: 'desenhada'
            };
        }
        layer._confronta.cor = normalizeColor(layer._confronta.cor);
        return layer._confronta;
    }

    function featureForLayer(layer) {
        const feature = layer.toGeoJSON();
        const meta = metadataFor(layer);
        feature.properties = Object.assign({}, feature.properties || {}, {
            confronta_id: String(meta.id),
            confronta_nome: meta.nome,
            confronta_cor: meta.cor,
            confronta_origem: meta.origem
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
        const value = feature ? areaHa(feature) : 0;
        const formatted = formatAreaHa(value);
        liveAreaValue.textContent = formatted;
        if (workflowStep === 'drawing') {
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
    function collectReferences(collection) {
        Object.values(collection || {}).forEach((layerData) => {
            if (!layerData || !layerData.disponivel || !Array.isArray(layerData.features)) return;
            layerData.features.forEach((feature) => {
                if (feature && feature.geometry) referenceFeatures.push({ label: layerData.label || 'Camada territorial', feature });
            });
        });
    }
    // O aviso operacional da gleba considera somente as camadas internas do SICAR.
    // Bases externas (PRODES, IBAMA, INCRA, CNUC/ICMBio etc.) e a sobreposição
    // com outros CARs continuam disponíveis no mapa/relatório, mas não poluem
    // o aviso superior durante desenho, importação ou edição de glebas.
    collectReferences(consulta.camadas);

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
            if (intersectionArea(feature, otherLayer.toGeoJSON()) > 0.01) labels.add(`Gleba: ${metadataFor(otherLayer).nome}`);
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
            return {
                id: String(meta.id),
                nome: meta.nome,
                cor: meta.cor,
                origem: meta.origem,
                area_ha: areaHa(feature),
                alertas: warningLabels(feature, layer),
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
        root.className = 'cf-gleba-popup';

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
        titleMeta.textContent = meta.origem === 'importada' ? 'Gleba importada' : 'Gleba desenhada';
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
        originValue.textContent = meta.origem === 'importada' ? 'Importada' : 'Desenhada';
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
        alert.textContent = alerts.length ? `Alertas: ${alerts.join(', ')}` : 'Nenhum alerta territorial identificado para esta gleba.';

        const actions = document.createElement('div');
        actions.className = 'cf-gleba-popup-actions';

        const edit = document.createElement('button');
        edit.type = 'button';
        edit.className = 'cf-gleba-popup-action is-secondary';
        edit.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4Z"></path></svg><span>' + (layer.editing && layer.editing.enabled() ? 'Salvar edição' : 'Editar gleba') + '</span>';

        const cancelEdit = document.createElement('button');
        cancelEdit.type = 'button';
        cancelEdit.className = 'cf-gleba-popup-action is-cancel';
        cancelEdit.hidden = !(layer.editing && layer.editing.enabled());
        cancelEdit.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"></path></svg><span>Cancelar edição</span>';

        edit.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            enableLayerEdit(layer, edit, cancelEdit);
        });
        cancelEdit.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            cancelLayerEdit(layer, edit, cancelEdit);
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

        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'cf-gleba-popup-action is-danger';
        del.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="m19 6-1 14H6L5 6"></path><path d="M10 11v6M14 11v6"></path></svg><span>Excluir</span>';
        del.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (!window.confirm(`Excluir a gleba "${meta.nome}" desta sessão?`)) return;
            drawnItems.removeLayer(layer);
            refresh();
            showOverlapAlert([]);
            map.closePopup();
        });

        actions.append(edit, cancelEdit, kml, del);

        body.append(metrics, carRow, alert, actions);
        root.append(head, body);
        return root;
    }

    function bindGlebaPopup(layer) {
        if (!layer || typeof layer.bindPopup !== 'function') return;
        try { layer.unbindPopup(); } catch (error) { /* noop */ }
        layer.bindPopup(() => buildGlebaPopup(layer), {
            maxWidth: 330,
            minWidth: 250,
            closeButton: true,
            autoPanPadding: [28, 28],
            className: 'confronta-gleba-leaflet-popup'
        });
    }

    function refreshGlebaPopups() {
        drawnItems.eachLayer((layer) => bindGlebaPopup(layer));
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
        if (statusElement) statusElement.textContent = layers.length ? `${layers.length} gleba${layers.length > 1 ? 's' : ''} salva${layers.length > 1 ? 's' : ''} nesta sessão.` : 'Nenhuma gleba salva.';
        if (downloadAllButton) downloadAllButton.disabled = layers.length === 0;
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
        if (listView) listView.hidden = true;
        if (workflow) workflow.hidden = false;
        [stepCreate, stepDrawing, stepName].forEach((element) => {
            if (element) element.hidden = true;
        });
        if (step === 'create' && stepCreate) {
            stepCreate.hidden = false;
            if (workflowTitle) workflowTitle.textContent = 'Criar gleba';
        }
        if (step === 'drawing' && stepDrawing) {
            stepDrawing.hidden = false;
            if (workflowTitle) workflowTitle.textContent = 'Desenhando no mapa';
        }
        if (step === 'name' && stepName) {
            stepName.hidden = false;
            if (workflowTitle) workflowTitle.textContent = 'Identifique a gleba';
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
        if (workflow) workflow.hidden = true;
        if (listView) listView.hidden = false;
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
            drawingHudColor.setAttribute('aria-label', `Cor da gleba: ${ALLOWED_COLORS[selectedColor]}. Alterar cor`);
        }
        map.getContainer().style.setProperty('--cf-gleba-vertex', selectedColor);
        if (drawHandler && workflowStep === 'drawing') {
            try { drawHandler.setOptions({ shapeOptions: glebaStyle(selectedColor, true) }); } catch (error) { /* noop */ }
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
            shapeOptions: glebaStyle(selectedColor, true)
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
            window.alert('A geometria da gleba não é válida.');
            return;
        }
        const name = sanitizeName(pendingName && pendingName.value, nextDefaultName());
        pendingLayer._confronta = {
            id: nextId(),
            nome: name,
            cor: selectedColor,
            origem: 'desenhada'
        };
        if (typeof pendingLayer.setStyle === 'function') pendingLayer.setStyle(glebaStyle(selectedColor, false));
        map.removeLayer(pendingLayer);
        drawnItems.addLayer(pendingLayer);
        const saved = pendingLayer;
        pendingLayer = null;
        refresh();
        const savedWarnings = warningLabels(featureForLayer(saved), saved);
        closeWorkflow();
        showOverlapAlert(savedWarnings, `Atenção — ${name}`);
    }

    function setPopupEditState(editButton, cancelButton, editing) {
        if (editButton) {
            const label = editButton.querySelector && editButton.querySelector('span');
            if (label) label.textContent = editing ? 'Salvar edição' : 'Editar gleba';
            else editButton.textContent = editing ? 'Salvar edição' : 'Editar';
            editButton.classList.toggle('is-editing', editing);
        }
        if (cancelButton) cancelButton.hidden = !editing;
    }

    function restoreLayerGeometry(layer, feature) {
        if (!layer || !feature || !feature.geometry || typeof layer.setLatLngs !== 'function') return false;
        try {
            const temporary = L.geoJSON(feature);
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

    function cancelLayerEdit(layer, editButton, cancelButton) {
        if (!layer || !layer.editing || !layer.editing.enabled()) return;
        layer.editing.disable();
        layer.off('edit', onLayerEditing);
        if (layer._confrontaEditSnapshot) restoreLayerGeometry(layer, layer._confrontaEditSnapshot);
        layer._confrontaEditSnapshot = null;
        hideLiveArea();
        setPopupEditState(editButton, cancelButton, false);
        persistSession();
        refresh();
        showOverlapAlert(warningLabels(featureForLayer(layer), layer), `Edição cancelada — ${metadataFor(layer).nome}`);
    }

    function enableLayerEdit(layer, button, cancelButton) {
        if (!layer || !layer.editing) return;
        const meta = metadataFor(layer);
        const editing = layer.editing.enabled();
        if (!editing) {
            layer._confrontaEditSnapshot = JSON.parse(JSON.stringify(layer.toGeoJSON()));
            map.getContainer().style.setProperty('--cf-gleba-vertex', meta.cor);
            layer.editing.enable();
            setPopupEditState(button, cancelButton, true);
            layer.on('edit', onLayerEditing);
            showLiveArea(layer.toGeoJSON());
        } else {
            layer.editing.disable();
            layer._confrontaEditSnapshot = null;
            setPopupEditState(button, cancelButton, false);
            layer.off('edit', onLayerEditing);
            hideLiveArea();
            persistSession();
            refresh();
            showOverlapAlert(warningLabels(featureForLayer(layer), layer), `Atenção — ${meta.nome}`);
        }
    }

    function onLayerEditing(event) {
        const layer = event.target;
        const feature = layer.toGeoJSON();
        showLiveArea(feature);
        showOverlapAlert(warningLabels(feature, layer), 'Atenção — edição');
    }

    function renderList() {
        if (!listElement) return;
        listElement.replaceChildren();
        drawnItems.getLayers().forEach((layer, index) => {
            const meta = metadataFor(layer);
            const feature = featureForLayer(layer);
            const alerts = warningLabels(feature, layer);

            const item = document.createElement('article');
            item.className = `gleba-item${alerts.length ? ' has-overlap' : ''}`;

            const top = document.createElement('div');
            top.className = 'gleba-item-top';
            const dot = document.createElement('span');
            dot.className = 'gleba-color-dot';
            dot.style.backgroundColor = meta.cor;
            if (meta.cor === '#FFFFFF') dot.classList.add('dot-white');
            const name = document.createElement('input');
            name.type = 'text';
            name.maxLength = 80;
            name.value = meta.nome;
            name.className = 'gleba-name-input';
            name.setAttribute('aria-label', `Nome da gleba ${index + 1}`);
            name.addEventListener('change', () => {
                meta.nome = sanitizeName(name.value, `Gleba ${index + 1}`);
                name.value = meta.nome;
                refresh();
            });
            top.append(dot, name);

            const detail = document.createElement('div');
            detail.className = 'gleba-item-details';

            const detailInfo = document.createElement('div');
            detailInfo.className = 'gleba-item-detail-info';

            const area = document.createElement('strong');
            area.textContent = `${areaHa(feature).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ha`;
            const origin = document.createElement('span');
            origin.textContent = meta.origem === 'importada' ? 'Importada' : 'Desenhada';
            detailInfo.append(area, origin);

            // MÓDULO 2 — ícone localizador da gleba.
            // O duplo clique reenquadra somente a geometria desta gleba.
            const locate = document.createElement('button');
            locate.type = 'button';
            locate.className = 'gleba-map-locator';
            locate.title = `Duplo clique para localizar ${meta.nome} no mapa`;
            locate.setAttribute('aria-label', `Localizar ${meta.nome} no mapa`);
            locate.innerHTML = `
                <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3Z"></path>
                    <path d="M9 3v15M15 6v15"></path>
                </svg>`;

            const fitGleba = () => {
                const bounds = layer.getBounds && layer.getBounds();
                if (!bounds || !bounds.isValid()) return;
                map.fitBounds(bounds, {
                    padding: [45, 45],
                    maxZoom: context.maxNativeZoom || context.maxZoom || 17,
                    animate: false
                });
            };

            locate.addEventListener('dblclick', (event) => {
                event.preventDefault();
                event.stopPropagation();
                fitGleba();
            });

            locate.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    fitGleba();
                }
            });

            detail.append(detailInfo, locate);

            const controls = document.createElement('div');
            controls.className = 'gleba-item-controls';

            const edit = document.createElement('button');
            edit.type = 'button';
            edit.className = 'gleba-mini-button';
            edit.textContent = 'Editar';
            edit.addEventListener('click', () => enableLayerEdit(layer, edit));

            const kml = document.createElement('button');
            kml.type = 'button';
            kml.className = 'gleba-mini-button';
            kml.textContent = 'KML';
            kml.addEventListener('click', () => downloadLayer(layer));

            const del = document.createElement('button');
            del.type = 'button';
            del.className = 'gleba-mini-button gleba-delete-button';
            del.textContent = 'Excluir';
            del.addEventListener('click', () => {
                if (!window.confirm(`Excluir a gleba "${meta.nome}" desta sessão?`)) return;
                drawnItems.removeLayer(layer);
                refresh();
                showOverlapAlert([]);
            });
            controls.append(edit, kml, del);

            item.append(top, detail, controls);
            if (alerts.length) {
                const warning = document.createElement('div');
                warning.className = 'gleba-overlap-warning';
                warning.textContent = `Alertas: ${alerts.join(', ')}`;
                item.appendChild(warning);
            }
            listElement.appendChild(item);
        });
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
        const temporary = L.geoJSON(feature, { style: glebaStyle(color, false) });
        const added = [];
        let part = 0;
        temporary.eachLayer((layer) => {
            layer._confronta = {
                id: part === 0 ? String(opts.id || nextId()) : nextId(),
                nome: part === 0 ? baseName : `${baseName} ${part + 1}`,
                cor: color,
                origem: opts.origem || 'importada'
            };
            if (typeof layer.setStyle === 'function') layer.setStyle(glebaStyle(color, false));
            drawnItems.addLayer(layer);
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
            window.alert('Não foi possível baixar esta gleba.');
        }
    }
    function downloadAll() {
        const layers = drawnItems.getLayers();
        if (!layers.length) return;
        try {
            downloadText(`glebas_${safeFilename(carCode)}.kml`, kmlDocument(layers, `Glebas - ${carCode}`));
        } catch (error) {
            console.error(error);
            window.alert('Não foi possível gerar o KML das glebas.');
        }
    }

    // ---------- Importação ----------
    function parseCoordinatesText(text) {
        const coordinates = String(text || '').trim().split(/\s+/).map((tuple) => {
            const parts = tuple.split(',').map(Number);
            return [parts[0], parts[1]];
        }).filter((coord) => Number.isFinite(coord[0]) && Number.isFinite(coord[1]));
        if (coordinates.length < 3) throw new Error('Polígono KML inválido.');
        const first = coordinates[0];
        const last = coordinates[coordinates.length - 1];
        if (first[0] !== last[0] || first[1] !== last[1]) coordinates.push(first.slice());
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
        const fallbackName = sanitizeName(file.name.replace(/\.[^.]+$/, ''), 'Gleba importada');
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
        showOverlapAlert(Array.from(warningSet), 'Gleba importada — atenção');
    }

    // ---------- Eventos de interface ----------
    if (startButton) startButton.addEventListener('click', () => {
        setSelectedColor(DEFAULT_COLOR);
        showWorkflowStep('create');
    });

    if (colorPicker) colorPicker.querySelectorAll('.gleba-color-swatch').forEach((button) => {
        button.addEventListener('click', () => setSelectedColor(button.dataset.color));
    });

    if (methodDraw) methodDraw.addEventListener('click', startDrawing);
    if (methodImport && importInput) methodImport.addEventListener('click', () => importInput.click());
    if (importInput) importInput.addEventListener('change', async () => {
        const file = importInput.files && importInput.files[0];
        setImportStatus('Importando e validando a geometria...', false);
        try {
            await importFile(file);
        } catch (error) {
            console.error(error);
            setImportStatus(error.message || 'Não foi possível importar a gleba.', true);
        } finally {
            importInput.value = '';
        }
    });

    if (pendingSave) pendingSave.addEventListener('click', savePendingLayer);
    if (pendingDiscard) pendingDiscard.addEventListener('click', () => {
        if (pendingLayer) {
            map.removeLayer(pendingLayer);
            pendingLayer = null;
        }
        showWorkflowStep('create');
        showOverlapAlert([]);
    });
    if (workflowCancel) workflowCancel.addEventListener('click', closeWorkflow);
    if (workflowBack) workflowBack.addEventListener('click', () => {
        if (workflowStep === 'drawing') {
            if (drawHandler) drawHandler.disable();
            drawHandler = null;
            hideLiveArea();
            stopDrawingHud();
            showWorkflowStep('create');
        } else if (workflowStep === 'name') {
            if (pendingLayer) {
                map.removeLayer(pendingLayer);
                pendingLayer = null;
            }
            showWorkflowStep('create');
        } else closeWorkflow();
    });
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
    if (downloadAllButton) downloadAllButton.addEventListener('click', downloadAll);

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

    map.on(L.Draw.Event.CREATED, (event) => {
        if (window.CONFRONTA_QUERY_DRAW_ACTIVE) return;
        if (!workflow || workflow.hidden || workflowStep !== 'drawing') return;
        if (drawHandler) {
            try { drawHandler.disable(); } catch (error) { /* noop */ }
            drawHandler = null;
        }
        stopDrawingHud();
        const feature = event.layer.toGeoJSON();
        if (!geometryLooksValid(feature)) {
            window.alert('A gleba desenhada é inválida. Tente novamente.');
            showWorkflowStep('create');
            return;
        }
        pendingLayer = event.layer;
        if (typeof pendingLayer.setStyle === 'function') pendingLayer.setStyle(glebaStyle(selectedColor, true));
        pendingLayer.addTo(map);
        if (pendingArea) pendingArea.textContent = formatAreaHa(areaHa(feature));
        hideLiveArea();
        if (pendingName) pendingName.value = '';
        showOverlapAlert(warningLabels(feature, null), 'Atenção — nova gleba');
        showWorkflowStep('name');
    });

    setSelectedColor(DEFAULT_COLOR);
    restoreSession();
    refresh();
})();
