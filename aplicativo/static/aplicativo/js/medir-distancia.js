(function () {
    'use strict';

    const button = document.getElementById('rail-measure-distance');
    const context = window.CONFRONTA_MAP_CONTEXT;
    if (!button || !context || !context.map || typeof L === 'undefined' || typeof L.Draw === 'undefined') return;

    const map = context.map;
    const measurements = L.featureGroup().addTo(map);
    let drawHandler = null;
    let mouseLatLng = null;
    let restoreDoubleClickZoom = false;
    let selectedLayer = null;
    let sequence = 0;

    const hud = document.createElement('section');
    hud.id = 'distance-measure-hud';
    hud.className = 'distance-measure-hud';
    hud.hidden = true;
    hud.innerHTML = `
        <div class="distance-measure-copy">
            <small>Medir Distância</small>
            <strong id="distance-measure-value">0 m</strong>
            <span id="distance-measure-help">Clique no mapa para marcar o primeiro ponto.</span>
        </div>
        <div class="distance-measure-actions">
            <button type="button" id="distance-measure-undo" disabled>Desfazer</button>
            <button type="button" id="distance-measure-finish" class="is-primary" disabled>Concluir</button>
            <button type="button" id="distance-measure-clear">Limpar</button>
            <button type="button" id="distance-measure-cancel" class="is-danger" aria-label="Cancelar medição">×</button>
        </div>`;

    const manager = document.createElement('section');
    manager.id = 'distance-measure-manager';
    manager.className = 'distance-measure-manager';
    manager.hidden = true;
    manager.setAttribute('aria-live', 'polite');
    manager.innerHTML = `
        <header>
            <div>
                <small>MEDIÇÃO SELECIONADA</small>
                <strong id="distance-selected-name">Medição</strong>
            </div>
            <button type="button" id="distance-selected-close" aria-label="Fechar ações da medição">×</button>
        </header>
        <div class="distance-measure-manager-value" id="distance-selected-value">0 m</div>
        <p id="distance-selected-help">Clique em Editar para mover os vértices da linha.</p>
        <div class="distance-measure-manager-actions">
            <button type="button" id="distance-selected-edit">Editar</button>
            <button type="button" id="distance-selected-delete" class="is-danger">Excluir</button>
        </div>`;

    const host = document.querySelector('.territorial-map-column') || document.body;
    host.append(hud, manager);

    const valueEl = hud.querySelector('#distance-measure-value');
    const helpEl = hud.querySelector('#distance-measure-help');
    const undoButton = hud.querySelector('#distance-measure-undo');
    const finishButton = hud.querySelector('#distance-measure-finish');
    const clearButton = hud.querySelector('#distance-measure-clear');
    const cancelButton = hud.querySelector('#distance-measure-cancel');

    const selectedName = manager.querySelector('#distance-selected-name');
    const selectedValue = manager.querySelector('#distance-selected-value');
    const selectedHelp = manager.querySelector('#distance-selected-help');
    const selectedEdit = manager.querySelector('#distance-selected-edit');
    const selectedDelete = manager.querySelector('#distance-selected-delete');
    const selectedClose = manager.querySelector('#distance-selected-close');

    function formatMeters(meters) {
        const value = Number(meters || 0);
        if (!Number.isFinite(value)) return '0 m';
        if (value >= 1000) {
            return `${(value / 1000).toLocaleString('pt-BR', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            })} km`;
        }
        return `${value.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} m`;
    }

    function flattenLatLngs(latlngs) {
        const result = [];
        (function walk(items) {
            (items || []).forEach((item) => {
                if (Array.isArray(item)) walk(item);
                else if (item && typeof item.lat === 'number' && typeof item.lng === 'number') result.push(item);
            });
        })(latlngs);
        return result;
    }

    function committedLatLngs() {
        if (!drawHandler || !Array.isArray(drawHandler._markers)) return [];
        return drawHandler._markers
            .map((marker) => marker && marker.getLatLng ? marker.getLatLng() : null)
            .filter(Boolean);
    }

    function distanceForLatLngs(latlngs) {
        let total = 0;
        for (let i = 1; i < latlngs.length; i += 1) total += map.distance(latlngs[i - 1], latlngs[i]);
        return total;
    }

    function liveDistance() {
        const points = committedLatLngs();
        let total = distanceForLatLngs(points);
        if (mouseLatLng && points.length) total += map.distance(points[points.length - 1], mouseLatLng);
        return total;
    }

    function syncHud() {
        const count = committedLatLngs().length;
        if (valueEl) valueEl.textContent = formatMeters(liveDistance());
        if (undoButton) undoButton.disabled = count === 0;
        if (finishButton) finishButton.disabled = count < 2;
        if (!helpEl) return;
        if (count === 0) helpEl.textContent = 'Clique no mapa para marcar o primeiro ponto.';
        else if (count === 1) helpEl.textContent = 'Marque o próximo ponto para medir o comprimento.';
        else helpEl.textContent = 'Continue marcando ou dê duplo clique para concluir.';
    }

    function setActive(active) {
        window.CONFRONTA_MEASURE_ACTIVE = Boolean(active);
        button.classList.toggle('is-active', Boolean(active));
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
        hud.hidden = !active;
        document.body.classList.toggle('is-measuring-distance', Boolean(active));
    }

    function detachLiveEvents() {
        map.off('mousemove', onMouseMove);
        map.off(L.Draw.Event.DRAWVERTEX, onDrawVertex);
        map.off('dblclick', onDoubleClick);
    }

    function stopDrawing() {
        if (drawHandler) {
            try { drawHandler.disable(); } catch (error) { /* noop */ }
            drawHandler = null;
        }
        mouseLatLng = null;
        detachLiveEvents();
        if (restoreDoubleClickZoom && map.doubleClickZoom) {
            try { map.doubleClickZoom.enable(); } catch (error) { /* noop */ }
        }
        restoreDoubleClickZoom = false;
        setActive(false);
    }

    function onMouseMove(event) {
        if (!window.CONFRONTA_MEASURE_ACTIVE) return;
        mouseLatLng = event && event.latlng ? event.latlng : null;
        syncHud();
    }

    function onDrawVertex() {
        mouseLatLng = null;
        window.setTimeout(syncHud, 0);
    }

    function finishDrawing() {
        if (!drawHandler || committedLatLngs().length < 2) return;
        if (typeof drawHandler.completeShape === 'function') drawHandler.completeShape();
    }

    function onDoubleClick(event) {
        if (!window.CONFRONTA_MEASURE_ACTIVE) return;
        if (event && event.originalEvent) {
            event.originalEvent.preventDefault();
            event.originalEvent.stopPropagation();
        }
        const points = committedLatLngs();
        if (points.length >= 3 && typeof drawHandler.deleteLastVertex === 'function') {
            const last = points[points.length - 1];
            const previous = points[points.length - 2];
            if (map.distance(previous, last) <= 2) drawHandler.deleteLastVertex();
        }
        window.setTimeout(finishDrawing, 0);
    }

    function startDrawing() {
        if (window.CONFRONTA_QUERY_DRAW_ACTIVE || document.body.classList.contains('is-drawing-gleba')) {
            window.alert('Conclua ou cancele o desenho atual antes de medir uma distância.');
            return;
        }
        hideManager();
        if (drawHandler) stopDrawing();

        restoreDoubleClickZoom = Boolean(map.doubleClickZoom && map.doubleClickZoom.enabled());
        if (restoreDoubleClickZoom) map.doubleClickZoom.disable();

        drawHandler = new L.Draw.Polyline(map, {
            repeatMode: false,
            shapeOptions: {
                color: '#00D47E',
                weight: 4,
                opacity: 1,
                lineCap: 'round',
                lineJoin: 'round',
                interactive: true
            },
            guidelineDistance: 12,
            metric: true,
            feet: false,
            nautic: false,
            showLength: false
        });

        map.on('mousemove', onMouseMove);
        map.on(L.Draw.Event.DRAWVERTEX, onDrawVertex);
        map.on('dblclick', onDoubleClick);
        drawHandler.enable();
        setActive(true);
        syncHud();
    }

    function updateMeasurement(layer) {
        const points = flattenLatLngs(layer.getLatLngs ? layer.getLatLngs() : []);
        const meters = distanceForLatLngs(points);
        layer._confrontaDistanceMeters = meters;
        if (layer.getTooltip()) layer.setTooltipContent(formatMeters(meters));
        if (selectedLayer === layer && selectedValue) selectedValue.textContent = formatMeters(meters);
        return meters;
    }

    function restoreLayerStyle(layer) {
        if (layer && layer.setStyle) layer.setStyle({ color: '#00D47E', weight: 4, opacity: 1 });
    }

    function selectLayer(layer, focusManager) {
        if (!layer) return;
        if (selectedLayer && selectedLayer !== layer) restoreLayerStyle(selectedLayer);
        selectedLayer = layer;
        measurements.eachLayer((item) => {
            if (item !== layer && item.editing && item.editing.enabled()) item.editing.disable();
        });
        layer.setStyle && layer.setStyle({ color: '#00D47E', weight: 6, opacity: 1 });
        selectedName.textContent = layer._confrontaDistanceName || 'Medição';
        selectedValue.textContent = formatMeters(updateMeasurement(layer));
        const editing = Boolean(layer.editing && layer.editing.enabled());
        selectedEdit.textContent = editing ? 'Salvar edição' : 'Editar';
        selectedHelp.textContent = editing
            ? 'Arraste os vértices da linha e depois clique em Salvar edição.'
            : 'Use Editar para mover os vértices ou Excluir para remover a medição.';
        manager.hidden = false;
        if (focusManager) selectedEdit.focus();
    }

    function hideManager() {
        if (selectedLayer && selectedLayer.editing && selectedLayer.editing.enabled()) selectedLayer.editing.disable();
        if (selectedLayer) restoreLayerStyle(selectedLayer);
        selectedLayer = null;
        manager.hidden = true;
    }

    function makeSvgPathAccessible(layer) {
        window.setTimeout(() => {
            const element = layer.getElement && layer.getElement();
            if (!element) return;
            element.setAttribute('tabindex', '0');
            element.setAttribute('role', 'button');
            element.setAttribute('aria-label', `${layer._confrontaDistanceName}. ${formatMeters(layer._confrontaDistanceMeters)}. Pressione Enter para editar ou excluir.`);
            element.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    selectLayer(layer, true);
                }
            });
        }, 0);
    }

    function addMeasurement(layer) {
        sequence += 1;
        layer._confrontaDistanceName = `Medição ${sequence}`;
        layer.setStyle && layer.setStyle({
            color: '#00D47E',
            weight: 4,
            opacity: 1,
            lineCap: 'round',
            lineJoin: 'round',
            interactive: true
        });

        layer.bindTooltip('0 m', {
            permanent: true,
            direction: 'center',
            className: 'distance-measure-label'
        });

        layer.on('click', (event) => {
            if (event && event.originalEvent) event.originalEvent.stopPropagation();
            selectLayer(layer, true);
        });
        layer.on('edit', () => {
            updateMeasurement(layer);
            makeSvgPathAccessible(layer);
        });

        measurements.addLayer(layer);
        updateMeasurement(layer);
        makeSvgPathAccessible(layer);
        stopDrawing();
        selectLayer(layer, false);
    }

    map.on(L.Draw.Event.CREATED, function (event) {
        if (!window.CONFRONTA_MEASURE_ACTIVE) return;
        if (!event || event.layerType !== 'polyline' || !event.layer) return;
        addMeasurement(event.layer);
    });

    button.addEventListener('click', function () {
        if (window.CONFRONTA_MEASURE_ACTIVE) stopDrawing();
        else startDrawing();
    });

    undoButton.addEventListener('click', function () {
        if (!drawHandler || typeof drawHandler.deleteLastVertex !== 'function') return;
        drawHandler.deleteLastVertex();
        mouseLatLng = null;
        syncHud();
    });
    finishButton.addEventListener('click', finishDrawing);
    clearButton.addEventListener('click', function () {
        measurements.clearLayers();
        hideManager();
        if (valueEl) valueEl.textContent = '0 m';
    });
    cancelButton.addEventListener('click', stopDrawing);

    selectedEdit.addEventListener('click', function () {
        if (!selectedLayer || !selectedLayer.editing) return;
        if (selectedLayer.editing.enabled()) {
            selectedLayer.editing.disable();
            updateMeasurement(selectedLayer);
            makeSvgPathAccessible(selectedLayer);
            selectLayer(selectedLayer, false);
        } else {
            selectedLayer.editing.enable();
            selectLayer(selectedLayer, false);
        }
    });

    selectedDelete.addEventListener('click', function () {
        if (!selectedLayer) return;
        const layer = selectedLayer;
        selectedLayer = null;
        measurements.removeLayer(layer);
        manager.hidden = true;
    });

    selectedClose.addEventListener('click', hideManager);

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            if (window.CONFRONTA_MEASURE_ACTIVE) stopDrawing();
            else if (!manager.hidden) hideManager();
        }
    });

    window.CONFRONTA_STOP_MEASURE_DISTANCE = stopDrawing;
})();
