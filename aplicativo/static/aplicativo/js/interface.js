(function () {
    'use strict';

    const context = window.CONFRONTA_MAP_CONTEXT;

    // MÓDULO 2 — alternância entre a visão operacional e o relatório atual.
    const viewButtons = document.querySelectorAll('[data-territorial-view]');
    const viewPanels = document.querySelectorAll('[data-territorial-view-panel]');

    function setTerritorialView(name) {
        viewButtons.forEach((button) => {
            const active = button.dataset.territorialView === name;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        viewPanels.forEach((panel) => {
            panel.hidden = panel.dataset.territorialViewPanel !== name;
        });
        if (name === 'map' && context && context.map) {
            window.setTimeout(() => context.map.invalidateSize(), 60);
        }
    }

    viewButtons.forEach((button) => {
        button.addEventListener('click', () => setTerritorialView(button.dataset.territorialView));
    });

    // Abas Camadas / Glebas do painel operacional.
    const tabButtons = document.querySelectorAll('[data-side-tab]');
    const tabPanels = document.querySelectorAll('[data-side-panel]');

    function setSideTab(name) {
        tabButtons.forEach((button) => {
            const active = button.dataset.sideTab === name;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        tabPanels.forEach((panel) => {
            panel.classList.toggle('is-active', panel.dataset.sidePanel === name);
        });
    }

    tabButtons.forEach((button) => {
        button.addEventListener('click', () => setSideTab(button.dataset.sideTab));
    });

    // Miniatura vetorial do perímetro do CAR no painel direito.
    function flattenCoordinates(geometry) {
        if (!geometry || !geometry.coordinates) return [];
        if (geometry.type === 'Polygon') return geometry.coordinates.flat();
        if (geometry.type === 'MultiPolygon') return geometry.coordinates.flat(2);
        return [];
    }

    function ringsForGeometry(geometry) {
        if (!geometry || !geometry.coordinates) return [];
        if (geometry.type === 'Polygon') return geometry.coordinates;
        if (geometry.type === 'MultiPolygon') return geometry.coordinates.flat();
        return [];
    }

    function renderCarPreview() {
        const container = document.getElementById('car-preview');
        const geometry = context && context.consulta && context.consulta.imovel && context.consulta.imovel.geometry;
        if (!container || !geometry) return;

        const coords = flattenCoordinates(geometry).filter((coord) => Array.isArray(coord) && coord.length >= 2);
        if (!coords.length) return;

        const xs = coords.map((coord) => Number(coord[0])).filter(Number.isFinite);
        const ys = coords.map((coord) => Number(coord[1])).filter(Number.isFinite);
        if (!xs.length || !ys.length) return;

        const minX = Math.min(...xs);
        const maxX = Math.max(...xs);
        const minY = Math.min(...ys);
        const maxY = Math.max(...ys);
        const dx = Math.max(maxX - minX, 1e-9);
        const dy = Math.max(maxY - minY, 1e-9);
        const size = 116;
        const pad = 10;
        const scale = Math.min((size - pad * 2) / dx, (size - pad * 2) / dy);
        const ox = (size - dx * scale) / 2;
        const oy = (size - dy * scale) / 2;

        const pathParts = ringsForGeometry(geometry).map((ring) => {
            if (!Array.isArray(ring) || !ring.length) return '';
            return ring.map((coord, index) => {
                const x = ox + (Number(coord[0]) - minX) * scale;
                const y = size - (oy + (Number(coord[1]) - minY) * scale);
                return `${index ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`;
            }).join(' ') + ' Z';
        }).join(' ');

        container.innerHTML = `
            <svg viewBox="0 0 ${size} ${size}" role="img" aria-label="Perímetro do CAR">
                <path d="${pathParts}" fill="rgba(167,213,176,.18)" stroke="#0B2D3C" stroke-width="2" vector-effect="non-scaling-stroke"></path>
            </svg>`;

        // MÓDULO 2 — duplo clique na representação do CAR reenquadra o imóvel no mapa.
        container.classList.add('is-map-locator');
        container.title = 'Duplo clique para localizar o CAR no mapa';
        container.setAttribute('role', 'button');
        container.setAttribute('tabindex', '0');

        const fitCar = () => {
            if (context && typeof context.fitCar === 'function') {
                context.fitCar();
                setTerritorialView('map');
            }
        };

        container.addEventListener('dblclick', fitCar);
        container.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                fitCar();
            }
        });
    }

    renderCarPreview();

    // Relatório atual: recebe a fotografia das glebas emitida por glebas.js.
    const reportGlebas = document.getElementById('report-glebas-list');
    const reportMap = document.getElementById('report-map-preview');
    let currentReportGlebas = [];

    function geometryCoordinates(geometry) {
        if (!geometry || !geometry.coordinates) return [];
        if (geometry.type === 'Polygon') return geometry.coordinates.flat();
        if (geometry.type === 'MultiPolygon') return geometry.coordinates.flat(2);
        return [];
    }

    function geometryRings(geometry) {
        if (!geometry || !geometry.coordinates) return [];
        if (geometry.type === 'Polygon') return geometry.coordinates;
        if (geometry.type === 'MultiPolygon') return geometry.coordinates.flat();
        return [];
    }

    function reportLabelPoint(geometry) {
        if (!geometry) return null;
        if (typeof turf !== 'undefined' && typeof turf.pointOnFeature === 'function') {
            try {
                const point = turf.pointOnFeature({ type: 'Feature', properties: {}, geometry });
                if (point && point.geometry && Array.isArray(point.geometry.coordinates)) return point.geometry.coordinates;
            } catch (error) {
                // O centro pelo envelope abaixo mantém o relatório disponível mesmo sem Turf.
            }
        }
        const coords = geometryCoordinates(geometry).filter((coord) => Array.isArray(coord) && coord.length >= 2);
        if (!coords.length) return null;
        const xs = coords.map((coord) => Number(coord[0])).filter(Number.isFinite);
        const ys = coords.map((coord) => Number(coord[1])).filter(Number.isFinite);
        if (!xs.length || !ys.length) return null;
        return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
    }

    function renderReportMap(items) {
        if (!reportMap) return;
        reportMap.replaceChildren();

        const carGeometry = context && context.consulta && context.consulta.imovel && context.consulta.imovel.geometry;
        if (!carGeometry) {
            reportMap.textContent = 'Geometria do CAR indisponível para o relatório.';
            return;
        }

        const safeItems = Array.isArray(items) ? items.filter((item) => item && item.geometry) : [];
        const allCoordinates = [carGeometry, ...safeItems.map((item) => item.geometry)]
            .flatMap(geometryCoordinates)
            .filter((coord) => Array.isArray(coord) && coord.length >= 2);

        const xs = allCoordinates.map((coord) => Number(coord[0])).filter(Number.isFinite);
        const ys = allCoordinates.map((coord) => Number(coord[1])).filter(Number.isFinite);
        if (!xs.length || !ys.length) return;

        const minX = Math.min(...xs);
        const maxX = Math.max(...xs);
        const minY = Math.min(...ys);
        const maxY = Math.max(...ys);
        const dx = Math.max(maxX - minX, 1e-9);
        const dy = Math.max(maxY - minY, 1e-9);
        const width = 760;
        const height = 440;
        const pad = 34;
        const scale = Math.min((width - pad * 2) / dx, (height - pad * 2) / dy);
        const ox = (width - dx * scale) / 2;
        const oy = (height - dy * scale) / 2;

        function project(coord) {
            return [
                ox + (Number(coord[0]) - minX) * scale,
                height - (oy + (Number(coord[1]) - minY) * scale)
            ];
        }

        function pathForGeometry(geometry) {
            return geometryRings(geometry).map((ring) => {
                if (!Array.isArray(ring) || !ring.length) return '';
                return ring.map((coord, index) => {
                    const point = project(coord);
                    return `${index ? 'L' : 'M'}${point[0].toFixed(2)} ${point[1].toFixed(2)}`;
                }).join(' ') + ' Z';
            }).join(' ');
        }

        const ns = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
        svg.setAttribute('role', 'img');
        svg.setAttribute('aria-label', 'Mapa vetorial do CAR com as glebas atuais');

        const background = document.createElementNS(ns, 'rect');
        background.setAttribute('x', '0');
        background.setAttribute('y', '0');
        background.setAttribute('width', String(width));
        background.setAttribute('height', String(height));
        background.setAttribute('fill', '#f7faf8');
        svg.appendChild(background);

        const carPath = document.createElementNS(ns, 'path');
        carPath.setAttribute('d', pathForGeometry(carGeometry));
        carPath.setAttribute('fill', 'rgba(167,213,176,.14)');
        carPath.setAttribute('stroke', '#0B2D3C');
        carPath.setAttribute('stroke-width', '3');
        carPath.setAttribute('vector-effect', 'non-scaling-stroke');
        svg.appendChild(carPath);

        safeItems.forEach((item) => {
            const path = document.createElementNS(ns, 'path');
            const color = String(item.cor || '#2563EB');
            path.setAttribute('d', pathForGeometry(item.geometry));
            path.setAttribute('fill', color === '#FFFFFF' ? 'rgba(255,255,255,.65)' : color);
            path.setAttribute('fill-opacity', color === '#FFFFFF' ? '1' : '.22');
            path.setAttribute('stroke', color === '#FFFFFF' ? '#65767c' : color);
            path.setAttribute('stroke-width', '3');
            path.setAttribute('vector-effect', 'non-scaling-stroke');
            svg.appendChild(path);

            const labelCoordinate = reportLabelPoint(item.geometry);
            if (!labelCoordinate) return;
            const labelPoint = project(labelCoordinate);

            const labelGroup = document.createElementNS(ns, 'g');
            const text = document.createElementNS(ns, 'text');
            text.setAttribute('x', labelPoint[0].toFixed(2));
            text.setAttribute('y', labelPoint[1].toFixed(2));
            text.setAttribute('text-anchor', 'middle');
            text.setAttribute('dominant-baseline', 'middle');
            text.setAttribute('class', 'report-gleba-label');
            text.textContent = item.nome || 'Gleba';
            labelGroup.appendChild(text);
            svg.appendChild(labelGroup);
        });

        reportMap.appendChild(svg);
    }

    function renderReportGlebas(items) {
        currentReportGlebas = Array.isArray(items) ? items : [];
        renderReportMap(currentReportGlebas);
        if (!reportGlebas) return;
        if (!currentReportGlebas.length) {
            reportGlebas.innerHTML = '<p class="muted">Nenhuma gleba salva nesta sessão.</p>';
            return;
        }
        reportGlebas.replaceChildren();
        currentReportGlebas.forEach((item) => {
            const row = document.createElement('div');
            const name = document.createElement('span');
            const value = document.createElement('strong');
            name.textContent = item.nome || 'Gleba';
            const alerts = Array.isArray(item.alertas) && item.alertas.length ? ` · ${item.alertas.join(', ')}` : '';
            value.textContent = `${Number(item.area_ha || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ha${alerts}`;
            row.append(name, value);
            reportGlebas.appendChild(row);
        });
    }

    window.addEventListener('confronta:glebas-updated', (event) => {
        renderReportGlebas(event.detail && event.detail.items);
    });

    if (window.CONFRONTA_GLEBAS_SNAPSHOT) renderReportGlebas(window.CONFRONTA_GLEBAS_SNAPSHOT);
    else renderReportMap([]);

    const printButton = document.getElementById('print-current-report');
    if (printButton) {
        printButton.addEventListener('click', () => {
            setTerritorialView('report');
            window.setTimeout(() => window.print(), 60);
        });
    }

    // ==================================================================
    // HOME v14 — ferramentas visíveis antes da consulta.
    // Elas não simulam operações sem um CAR: apenas orientam o usuário a
    // informar o número no campo superior e executar a busca real existente.
    // ==================================================================
    const selectCarMessage = document.getElementById('select-car-message');
    let selectCarMessageTimer = null;

    function showSelectCarMessage() {
        if (!selectCarMessage) return;
        selectCarMessage.hidden = false;
        selectCarMessage.classList.remove('is-visible');
        window.requestAnimationFrame(() => selectCarMessage.classList.add('is-visible'));
        window.clearTimeout(selectCarMessageTimer);
        selectCarMessageTimer = window.setTimeout(() => {
            selectCarMessage.classList.remove('is-visible');
            window.setTimeout(() => { selectCarMessage.hidden = true; }, 180);
        }, 2600);
        const searchInput = document.querySelector('.client-global-search input[name="car"]');
        if (searchInput) searchInput.focus({ preventScroll: true });
    }

    document.querySelectorAll('[data-requires-car="1"]').forEach((button) => {
        button.addEventListener('click', (event) => {
            event.preventDefault();
            showSelectCarMessage();
        });
    });

    // ==================================================================
    // HOME v14 — barra lateral operacional do imóvel consultado.
    // Somente coordena componentes visuais já existentes; o motor GIS,
    // as camadas e a persistência temporária das glebas permanecem iguais.
    // ==================================================================
    const railAlerts = document.getElementById('rail-alerts');
    const railLayers = document.getElementById('rail-layers');
    const railFitCar = document.getElementById('rail-fit-car');
    const railReport = document.getElementById('rail-report');
    const railGlebas = document.getElementById('rail-glebas');
    const railSummary = document.getElementById('rail-summary');
    const layerDrawer = document.getElementById('map-layer-drawer');
    const layerDrawerClose = document.getElementById('close-layer-drawer');
    const toolDrawer = document.getElementById('territorial-side-panel');
    const toolDrawerClose = document.getElementById('territorial-tool-close');
    const toolKicker = document.getElementById('territorial-tool-kicker');
    const toolTitle = document.getElementById('territorial-tool-title');
    const propertySummary = document.getElementById('map-property-summary');
    const propertySummaryCollapse = document.getElementById('toggle-map-summary-collapse');
    const propertySummaryCollapseLabel = propertySummaryCollapse ? propertySummaryCollapse.querySelector('span') : null;
    const summaryGlebasCount = document.getElementById('map-summary-glebas-count');
    const restrictionCount = document.getElementById('map-summary-restrictions-count');
    const restrictionDetails = document.getElementById('map-summary-restrictions-details');
    const reportMaximizeButton = document.getElementById('toggle-report-maximize');
    const reportMaximizeLabel = reportMaximizeButton ? reportMaximizeButton.querySelector('.report-maximize-label') : null;
    const reportMaximizeIcon = reportMaximizeButton ? reportMaximizeButton.querySelector('.report-maximize-icon') : null;

    function setRailPressed(button, active) {
        if (!button) return;
        button.classList.toggle('is-active', Boolean(active));
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
    }

    function closeLayerDrawer() {
        if (layerDrawer) layerDrawer.hidden = true;
        setRailPressed(railLayers, false);
    }

    function openLayerDrawer() {
        if (!layerDrawer) return;
        const willOpen = layerDrawer.hidden;
        closeToolDrawer();
        layerDrawer.hidden = !willOpen;
        setRailPressed(railLayers, willOpen);
    }

    function closeToolDrawer() {
        if (!toolDrawer) return;
        toolDrawer.hidden = true;
        toolDrawer.classList.remove('is-open', 'is-report-mode', 'is-glebas-mode', 'is-alerts-mode', 'is-report-maximized');
        if (reportMaximizeButton) reportMaximizeButton.setAttribute('aria-pressed', 'false');
        if (reportMaximizeLabel) reportMaximizeLabel.textContent = 'Maximizar';
        if (reportMaximizeIcon) reportMaximizeIcon.textContent = '↗';
        setRailPressed(railReport, false);
        setRailPressed(railGlebas, false);
        setRailPressed(railAlerts, false);
        if (context && context.map) window.setTimeout(() => context.map.invalidateSize(), 40);
    }

    function openToolDrawer(mode) {
        if (!toolDrawer) return;
        closeLayerDrawer();
        toolDrawer.hidden = false;
        toolDrawer.classList.add('is-open');
        toolDrawer.classList.toggle('is-report-mode', mode === 'report');
        toolDrawer.classList.toggle('is-glebas-mode', mode === 'glebas');
        toolDrawer.classList.toggle('is-alerts-mode', mode === 'alerts');
        if (mode !== 'report') toolDrawer.classList.remove('is-report-maximized');

        if (mode === 'report') {
            setTerritorialView('report');
            if (toolKicker) toolKicker.textContent = 'RELATÓRIO';
            if (toolTitle) toolTitle.textContent = 'Inteligência do imóvel';
            setRailPressed(railReport, true);
            setRailPressed(railGlebas, false);
            setRailPressed(railAlerts, false);
        } else if (mode === 'glebas') {
            setTerritorialView('map');
            setSideTab('glebas');
            if (toolKicker) toolKicker.textContent = 'PROJETO';
            if (toolTitle) toolTitle.textContent = 'Glebas';
            setRailPressed(railGlebas, true);
            setRailPressed(railReport, false);
            setRailPressed(railAlerts, false);
        } else if (mode === 'alerts') {
            setTerritorialView('map');
            setSideTab('layers');
            if (toolKicker) toolKicker.textContent = 'ALERTAS';
            if (toolTitle) toolTitle.textContent = 'Alertas do imóvel';
            setRailPressed(railAlerts, true);
            setRailPressed(railReport, false);
            setRailPressed(railGlebas, false);
            window.setTimeout(() => {
                const target = toolDrawer.querySelector('.alert-title') || toolDrawer.querySelector('.property-alert-list');
                if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 80);
        }
        if (context && context.map) window.setTimeout(() => context.map.invalidateSize(), 40);
    }

    function syncLayerEye(key, visible) {
        document.querySelectorAll(`[data-layer-eye="${CSS.escape(key)}"]`).forEach((button) => {
            button.classList.toggle('is-active', Boolean(visible));
            button.setAttribute('aria-pressed', visible ? 'true' : 'false');
        });
    }

    if (reportMaximizeButton && toolDrawer) {
        reportMaximizeButton.addEventListener('click', () => {
            const maximized = !toolDrawer.classList.contains('is-report-maximized');
            toolDrawer.classList.toggle('is-report-maximized', maximized);
            reportMaximizeButton.setAttribute('aria-pressed', maximized ? 'true' : 'false');
            reportMaximizeButton.setAttribute('title', maximized ? 'Restaurar relatório' : 'Maximizar relatório');
            if (reportMaximizeLabel) reportMaximizeLabel.textContent = maximized ? 'Restaurar' : 'Maximizar';
            if (reportMaximizeIcon) reportMaximizeIcon.textContent = maximized ? '↙' : '↗';
            if (context && context.map) window.setTimeout(() => context.map.invalidateSize(), 60);
        });
    }

    document.querySelectorAll('[data-layer-eye]').forEach((button) => {
        button.addEventListener('click', () => {
            const key = button.dataset.layerEye;
            if (!key || !context || typeof context.setLayerVisible !== 'function') return;
            const currentlyVisible = button.getAttribute('aria-pressed') === 'true';
            const nextVisible = !currentlyVisible;
            context.setLayerVisible(key, nextVisible);
            syncLayerEye(key, nextVisible);
        });
    });

    // Se uma camada for alterada por outro controle já existente, mantém o
    // ícone de olho da nova gaveta sincronizado.
    document.querySelectorAll('.layer-toggle').forEach((toggle) => {
        toggle.addEventListener('change', function () {
            if (this.dataset.layer) syncLayerEye(this.dataset.layer, this.checked);
        });
    });


    if (railAlerts) {
        railAlerts.addEventListener('click', () => {
            if (toolDrawer && !toolDrawer.hidden && toolDrawer.classList.contains('is-alerts-mode')) closeToolDrawer();
            else openToolDrawer('alerts');
        });
    }

    if (railLayers) railLayers.addEventListener('click', openLayerDrawer);
    if (layerDrawerClose) layerDrawerClose.addEventListener('click', closeLayerDrawer);

    if (railFitCar) {
        railFitCar.addEventListener('click', () => {
            closeLayerDrawer();
            closeToolDrawer();
            if (context && typeof context.fitCar === 'function') context.fitCar();
        });
    }

    if (railReport) {
        railReport.addEventListener('click', () => {
            if (toolDrawer && !toolDrawer.hidden && toolDrawer.classList.contains('is-report-mode')) closeToolDrawer();
            else openToolDrawer('report');
        });
    }

    if (railGlebas) {
        railGlebas.addEventListener('click', () => {
            if (toolDrawer && !toolDrawer.hidden && toolDrawer.classList.contains('is-glebas-mode')) closeToolDrawer();
            else openToolDrawer('glebas');
        });
    }

    if (toolDrawerClose) toolDrawerClose.addEventListener('click', closeToolDrawer);

    if (propertySummaryCollapse && propertySummary) {
        propertySummaryCollapse.addEventListener('click', () => {
            const collapsed = !propertySummary.classList.contains('is-collapsed');
            propertySummary.classList.toggle('is-collapsed', collapsed);
            propertySummaryCollapse.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            propertySummaryCollapse.setAttribute('title', collapsed ? 'Expandir informações' : 'Recolher informações');
            if (propertySummaryCollapseLabel) propertySummaryCollapseLabel.textContent = collapsed ? 'Expandir' : 'Recolher';
        });
    }

    function updateSummaryGlebas(items) {
        if (!summaryGlebasCount) return;
        const quantidade = Array.isArray(items) ? items.length : 0;
        summaryGlebasCount.textContent = `${quantidade} ${quantidade === 1 ? 'gleba' : 'glebas'}`;
    }
    window.addEventListener('confronta:glebas-updated', (event) => {
        updateSummaryGlebas(event.detail && event.detail.items);
    });
    if (window.CONFRONTA_GLEBAS_SNAPSHOT) updateSummaryGlebas(window.CONFRONTA_GLEBAS_SNAPSHOT);

    if (railSummary && propertySummary) {
        railSummary.addEventListener('click', () => {
            const willShow = propertySummary.hidden;
            propertySummary.hidden = !willShow;
            setRailPressed(railSummary, willShow);
        });
    }

    // A faixa inferior recebe do backend somente as classes de restrição
    // aprovadas: CAR×CAR, Assentamento, Quilombola e PRODES. IBAMA permanece
    // como atenção que exige confirmação oficial e APA é informação territorial.
    if (restrictionCount && context && context.consulta) {
        const restrictionData = context.consulta.restricoes ||
            (context.consulta.alertas && context.consulta.alertas.restricoes) || {};
        const identified = Number(restrictionData.quantidade || 0);
        const tipos = Array.isArray(restrictionData.tipos) ? restrictionData.tipos : [];
        if (identified > 0) {
            restrictionCount.textContent = `${identified} ${identified === 1 ? 'identificado' : 'identificados'}`;
            restrictionCount.classList.add('has-restrictions');
            restrictionCount.classList.remove('no-restrictions');
            if (restrictionDetails) {
                restrictionDetails.textContent = tipos.join(' • ');
                restrictionDetails.hidden = false;
            }
            restrictionCount.parentElement?.setAttribute('title', tipos.join(' • '));
        } else {
            restrictionCount.textContent = 'Nenhum identificado';
            restrictionCount.classList.add('no-restrictions');
            restrictionCount.classList.remove('has-restrictions');
            if (restrictionDetails) {
                restrictionDetails.textContent = '';
                restrictionDetails.hidden = true;
            }
        }
    }

    const summaryCards = [
        document.getElementById('summary-card-alerts'),
        document.getElementById('summary-card-outros-cars'),
        document.getElementById('summary-card-prodes'),
        document.getElementById('summary-card-ibama')
    ].filter(Boolean);

    summaryCards.forEach((card) => {
        card.addEventListener('click', () => openToolDrawer('alerts'));
    });

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        closeLayerDrawer();
        closeToolDrawer();
    });
})();
