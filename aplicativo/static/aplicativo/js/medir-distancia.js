(function () {
    'use strict';

    const button = document.getElementById('rail-measure-distance');
    const context = window.CONFRONTA_MAP_CONTEXT;
    if (!button || !context || !context.map || typeof L === 'undefined' || typeof L.Draw === 'undefined') return;

    const map = context.map;
    const measurements = L.featureGroup().addTo(map);
    let drawHandler = null;
    let mouseLatLng = null;

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
    const host = document.querySelector('.territorial-map-column') || document.body;
    host.appendChild(hud);

    const valueEl = hud.querySelector('#distance-measure-value');
    const helpEl = hud.querySelector('#distance-measure-help');
    const undoButton = hud.querySelector('#distance-measure-undo');
    const finishButton = hud.querySelector('#distance-measure-finish');
    const clearButton = hud.querySelector('#distance-measure-clear');
    const cancelButton = hud.querySelector('#distance-measure-cancel');

    function formatMeters(meters) {
        const value = Number(meters || 0);
        if (!Number.isFinite(value)) return '0 m';
        if (value < 1000) return `${value.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} m`;
        return `${value.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} m`;
    }

    function committedLatLngs() {
        if (!drawHandler || !Array.isArray(drawHandler._markers)) return [];
        return drawHandler._markers
            .map((marker) => marker && marker.getLatLng ? marker.getLatLng() : null)
            .filter(Boolean);
    }

    function distanceForLatLngs(latlngs) {
        let total = 0;
        for (let i = 1; i < latlngs.length; i += 1) {
            total += map.distance(latlngs[i - 1], latlngs[i]);
        }
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
        if (helpEl) {
            if (count === 0) helpEl.textContent = 'Clique no mapa para marcar o primeiro ponto.';
            else if (count === 1) helpEl.textContent = 'Marque o próximo ponto para medir o comprimento.';
            else helpEl.textContent = 'Continue marcando pontos ou clique em Concluir.';
        }
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
    }

    function stopDrawing(options) {
        const opts = options || {};
        if (drawHandler) {
            try { drawHandler.disable(); } catch (error) { /* noop */ }
            drawHandler = null;
        }
        mouseLatLng = null;
        detachLiveEvents();
        setActive(false);
        if (opts.clear) measurements.clearLayers();
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

    function startDrawing() {
        if (window.CONFRONTA_QUERY_DRAW_ACTIVE || document.body.classList.contains('is-drawing-gleba')) {
            window.alert('Conclua ou cancele o desenho atual antes de medir uma distância.');
            return;
        }
        if (drawHandler) stopDrawing();
        mouseLatLng = null;
        drawHandler = new L.Draw.Polyline(map, {
            repeatMode: false,
            shapeOptions: {
                color: '#00D47E',
                weight: 4,
                opacity: 1,
                lineCap: 'round',
                lineJoin: 'round'
            },
            guidelineDistance: 12,
            metric: true,
            feet: false,
            nautic: false,
            showLength: false
        });
        map.on('mousemove', onMouseMove);
        map.on(L.Draw.Event.DRAWVERTEX, onDrawVertex);
        drawHandler.enable();
        setActive(true);
        syncHud();
    }

    function finishDrawing() {
        if (!drawHandler || committedLatLngs().length < 2) return;
        if (typeof drawHandler.completeShape === 'function') drawHandler.completeShape();
    }

    function addMeasurement(layer) {
        const latlngs = layer.getLatLngs ? layer.getLatLngs() : [];
        const flat = Array.isArray(latlngs[0]) ? latlngs.flat(Infinity).filter((item) => item && typeof item.lat === 'number') : latlngs;
        const meters = distanceForLatLngs(flat);
        layer.setStyle && layer.setStyle({ color: '#00D47E', weight: 4, opacity: 1 });
        layer.bindTooltip(formatMeters(meters), {
            permanent: true,
            direction: 'center',
            className: 'distance-measure-label'
        });
        measurements.addLayer(layer);
        if (valueEl) valueEl.textContent = formatMeters(meters);
        stopDrawing();
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

    if (undoButton) undoButton.addEventListener('click', function () {
        if (!drawHandler || typeof drawHandler.deleteLastVertex !== 'function') return;
        drawHandler.deleteLastVertex();
        mouseLatLng = null;
        syncHud();
    });
    if (finishButton) finishButton.addEventListener('click', finishDrawing);
    if (clearButton) clearButton.addEventListener('click', function () {
        measurements.clearLayers();
        if (valueEl) valueEl.textContent = '0 m';
    });
    if (cancelButton) cancelButton.addEventListener('click', function () { stopDrawing(); });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && window.CONFRONTA_MEASURE_ACTIVE) stopDrawing();
    });

    window.CONFRONTA_STOP_MEASURE_DISTANCE = stopDrawing;
})();
