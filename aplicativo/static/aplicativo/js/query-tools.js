(function () {
    'use strict';

    const openButton = document.getElementById('open-new-query');
    const modal = document.getElementById('new-query-modal');
    if (!openButton || !modal) return;

    const dialog = modal.querySelector('.new-query-dialog');
    const views = Array.from(modal.querySelectorAll('[data-query-view]'));
    const modeButtons = Array.from(modal.querySelectorAll('[data-query-mode]'));
    const closeButtons = Array.from(modal.querySelectorAll('[data-close-new-query]'));
    const backButtons = Array.from(modal.querySelectorAll('[data-query-back]'));
    const fileInput = document.getElementById('query-file');
    const fileName = document.getElementById('query-file-name');
    const startDraw = document.getElementById('start-query-draw');
    const drawHud = document.getElementById('query-draw-hud');
    const cancelDraw = document.getElementById('cancel-query-draw');
    const geometryForm = document.getElementById('query-geometry-form');
    const geometryInput = document.getElementById('query-geometry-input');

    let previousFocus = null;
    let drawHandler = null;
    let drawCreatedHandler = null;
    let queryPreview = null;

    function showView(name) {
        views.forEach((view) => {
            const active = view.dataset.queryView === name;
            view.hidden = !active;
        });

        window.setTimeout(() => {
            const activeView = modal.querySelector(`[data-query-view="${name}"]:not([hidden])`);
            const field = activeView && activeView.querySelector('input:not([type="hidden"]), button:not([disabled])');
            if (field) field.focus({ preventScroll: true });
        }, 0);
    }

    function openModal(view) {
        previousFocus = document.activeElement;
        showView(view || 'menu');
        modal.hidden = false;
        modal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('new-query-open');
        window.setTimeout(() => {
            const target = modal.querySelector('[data-query-view="menu"] [data-query-mode]:not([disabled])') || dialog;
            if (target && typeof target.focus === 'function') target.focus({ preventScroll: true });
        }, 0);
    }

    function closeModal(options) {
        const opts = options || {};
        modal.hidden = true;
        modal.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('new-query-open');
        showView('menu');
        if (!opts.skipRestoreFocus && previousFocus && typeof previousFocus.focus === 'function') {
            previousFocus.focus({ preventScroll: true });
        }
    }

    function mapContext() {
        return window.CONFRONTA_MAP_CONTEXT || null;
    }

    function stopQueryDrawing() {
        if (drawHandler) {
            try { drawHandler.disable(); } catch (error) { /* noop */ }
            drawHandler = null;
        }
        const context = mapContext();
        if (context && context.map && drawCreatedHandler) {
            context.map.off(L.Draw.Event.CREATED, drawCreatedHandler);
        }
        drawCreatedHandler = null;
        window.CONFRONTA_QUERY_DRAW_ACTIVE = false;
        if (drawHud) drawHud.hidden = true;
        if (queryPreview && context && context.map) {
            try { context.map.removeLayer(queryPreview); } catch (error) { /* noop */ }
        }
        queryPreview = null;
    }

    function startQueryDrawing() {
        const context = mapContext();
        if (!context || !context.map || typeof L === 'undefined' || typeof L.Draw === 'undefined') return;
        if (!geometryForm || !geometryInput) return;

        if (window.CONFRONTA_MEASURE_ACTIVE && typeof window.CONFRONTA_STOP_MEASURE_DISTANCE === 'function') {
            window.CONFRONTA_STOP_MEASURE_DISTANCE();
        }
        closeModal({ skipRestoreFocus: true });
        stopQueryDrawing();
        window.CONFRONTA_QUERY_DRAW_ACTIVE = true;
        if (drawHud) drawHud.hidden = false;

        drawCreatedHandler = function (event) {
            if (!window.CONFRONTA_QUERY_DRAW_ACTIVE) return;
            if (!event || !event.layer || typeof event.layer.toGeoJSON !== 'function') return;

            const feature = event.layer.toGeoJSON();
            const geometry = feature && feature.geometry;
            if (!geometry || !['Polygon', 'MultiPolygon'].includes(geometry.type)) {
                window.alert('A área desenhada não é um polígono válido.');
                return;
            }

            const localContext = mapContext();
            if (localContext && localContext.map) {
                queryPreview = event.layer;
                if (typeof queryPreview.setStyle === 'function') {
                    queryPreview.setStyle({ color: '#4FA36A', weight: 3, fillColor: '#4FA36A', fillOpacity: 0.16 });
                }
                queryPreview.addTo(localContext.map);
            }

            geometryInput.value = JSON.stringify(geometry);
            if (drawHandler) {
                try { drawHandler.disable(); } catch (error) { /* noop */ }
                drawHandler = null;
            }
            if (drawHud) drawHud.hidden = true;
            window.CONFRONTA_QUERY_DRAW_ACTIVE = false;
            if (localContext && localContext.map && drawCreatedHandler) {
                localContext.map.off(L.Draw.Event.CREATED, drawCreatedHandler);
            }
            drawCreatedHandler = null;
            geometryForm.submit();
        };

        context.map.on(L.Draw.Event.CREATED, drawCreatedHandler);
        drawHandler = new L.Draw.Polygon(context.map, {
            allowIntersection: false,
            showArea: true,
            shapeOptions: {
                color: '#4FA36A',
                weight: 3,
                opacity: 1,
                fillColor: '#4FA36A',
                fillOpacity: 0.14
            }
        });
        drawHandler.enable();
    }

    window.CONFRONTA_OPEN_QUERY_MODAL = function (view) {
        openModal(view || 'menu');
    };

    openButton.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        openModal('menu');
    });
    const topbarSearchForm = document.querySelector('form.topbar-car-search');
    if (topbarSearchForm) {
        topbarSearchForm.addEventListener('submit', (event) => {
            const input = topbarSearchForm.querySelector('input[name="car"]');
            if (!input) return;
            input.value = input.value.trim();
            if (input.value) return;
            event.preventDefault();
            input.focus({ preventScroll: true });
        });
    }

    closeButtons.forEach((button) => button.addEventListener('click', () => closeModal()));
    backButtons.forEach((button) => button.addEventListener('click', () => showView('menu')));

    modeButtons.forEach((button) => {
        button.addEventListener('click', () => {
            if (button.disabled) return;
            const mode = button.dataset.queryMode;
            if (!mode) return;
            showView(mode);
        });
    });

    if (fileInput && fileName) {
        fileInput.addEventListener('change', () => {
            const file = fileInput.files && fileInput.files[0];
            fileName.textContent = file ? file.name : 'Nenhum arquivo selecionado';
        });
    }

    if (startDraw) startDraw.addEventListener('click', startQueryDrawing);
    if (cancelDraw) cancelDraw.addEventListener('click', stopQueryDrawing);

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        if (window.CONFRONTA_QUERY_DRAW_ACTIVE) {
            stopQueryDrawing();
            return;
        }
        if (!modal.hidden) closeModal();
    });

    if (dialog) {
        dialog.addEventListener('keydown', (event) => {
            if (event.key !== 'Tab') return;
            const focusable = Array.from(dialog.querySelectorAll('button:not([disabled]):not([hidden]), input:not([disabled]):not([hidden])'))
                .filter((item) => item.offsetParent !== null);
            if (!focusable.length) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        });
    }
})();
