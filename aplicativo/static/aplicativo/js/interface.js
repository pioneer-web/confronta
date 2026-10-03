(function () {
    'use strict';

    const context = window.CONFRONTA_MAP_CONTEXT;

    const messageStack = document.querySelector('.cf-message-stack');
    if (messageStack) {
        let notificationTimer = null;
        let activeNotification = null;
        const dismissNotification = () => {
            window.clearTimeout(notificationTimer);
            notificationTimer = null;
            if (activeNotification) activeNotification.remove();
            activeNotification = null;
            messageStack.hidden = true;
        };
        const activateLatestNotification = () => {
            const notices = Array.from(messageStack.querySelectorAll('.query-result-notice'));
            if (!notices.length) {
                window.clearTimeout(notificationTimer);
                notificationTimer = null;
                activeNotification = null;
                messageStack.hidden = true;
                return;
            }
            const latest = notices[notices.length - 1];
            if (latest === activeNotification && !messageStack.hidden) return;
            window.clearTimeout(notificationTimer);
            notices.slice(0, -1).forEach((notice) => notice.remove());
            activeNotification = latest;
            messageStack.hidden = false;
            const duration = latest.classList.contains('alert-danger') ? 8000 : 5000;
            notificationTimer = window.setTimeout(dismissNotification, duration);
        };
        messageStack.addEventListener('click', (event) => {
            if (event.target.closest('.query-result-notice-close')) dismissNotification();
        });
        new MutationObserver(activateLatestNotification).observe(messageStack, { childList: true, subtree: true });
        activateLatestNotification();
    }

    // Busca universal: CAR, coordenada ou município. Município nunca cai no
    // POST de CAR; sugestões consultam somente atributos leves da base SICAR.
    const universalSearch = document.querySelector('.topbar-car-search');
    if (universalSearch) {
        const field = universalSearch.querySelector('input[name="car"]');
        const config = document.getElementById('app-config');
        const feedback = document.createElement('div');
        feedback.className = 'universal-search-feedback';
        feedback.setAttribute('aria-live', 'polite');
        universalSearch.insertAdjacentElement('afterend', feedback);
        const suggestions = document.createElement('div');
        suggestions.className = 'municipality-suggestions';
        suggestions.hidden = true;
        suggestions.setAttribute('role', 'listbox');
        universalSearch.insertAdjacentElement('afterend', suggestions);
        let debounceTimer = null;
        let requestController = null;
        let requestSequence = 0;
        let selectedMunicipalityLabel = null;

        function resolveInput(raw) {
            const value = raw.trim();
            const compact = value.replace(/[^a-z0-9]/gi, '').toUpperCase();
            const looksCar = /^[A-Z]{2}\d/.test(compact) || (compact.length >= 28 && /^[A-Z0-9]+$/.test(compact));
            if (looksCar) {
                const match = compact.match(/^([A-Z]{2})(\d{7})([A-Z0-9]{32})$/);
                return match ? { type: 'car', value: `${match[1]}-${match[2]}-${match[3]}` } : { type: 'invalid-car' };
            }
            const coordinate = value.match(/^\s*([+-]?\d+(?:[.,]\d+)?)\s*(?:[,;]|\s)\s*([+-]?\d+(?:[.,]\d+)?)\s*$/);
            const resemblesCoordinate = /^[+-]?(?:\d|\.\d)/.test(value) && /[,;\s]/.test(value);
            if (coordinate) {
                const latitude = Number(coordinate[1].replace(',', '.'));
                const longitude = Number(coordinate[2].replace(',', '.'));
                return Number.isFinite(latitude) && Number.isFinite(longitude) && latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180
                    ? { type: 'coordinate', latitude, longitude } : { type: 'invalid-coordinate' };
            }
            if (resemblesCoordinate) return { type: 'invalid-coordinate' };
            return value ? { type: 'municipality', value } : { type: 'empty' };
        }

        function closeSuggestions() {
            suggestions.hidden = true;
            suggestions.replaceChildren();
            requestSequence += 1;
            if (requestController) requestController.abort();
            requestController = null;
        }
        function showFeedback(message) {
            selectedMunicipalityLabel = null;
            feedback.classList.remove('is-municipality-result');
            feedback.replaceChildren();
            feedback.textContent = message || '';
        }

        function showSelectedMunicipality(label) {
            selectedMunicipalityLabel = label;
            feedback.classList.add('is-municipality-result');
            feedback.replaceChildren();
            const title = document.createElement('small');
            title.className = 'municipality-result-title';
            title.textContent = 'Município';
            const name = document.createElement('strong');
            name.className = 'municipality-result-name';
            name.textContent = label;
            const close = document.createElement('button');
            close.type = 'button';
            close.className = 'municipality-result-close';
            close.setAttribute('aria-label', 'Fechar município selecionado');
            close.textContent = '×';
            close.addEventListener('click', () => {
                if (selectedMunicipalityLabel && field.value === selectedMunicipalityLabel) field.value = '';
                showFeedback('');
                field.focus({ preventScroll: true });
            });
            feedback.append(title, name, close);
        }

        async function loadMunicipalities(query) {
            if (!config?.dataset.municipalitiesUrl || query.trim().length < 2) { closeSuggestions(); return []; }
            if (requestController) requestController.abort();
            requestController = new AbortController();
            const current = ++requestSequence;
            const url = new URL(config.dataset.municipalitiesUrl, window.location.origin);
            url.searchParams.set('q', query.trim());
            const response = await fetch(url, { signal: requestController.signal, headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            const payload = await response.json();
            if (current !== requestSequence) return [];
            if (!response.ok) throw new Error(payload.erro || 'Não foi possível buscar municípios.');
            return Array.isArray(payload.resultados) ? payload.resultados : [];
        }

        function selectMunicipality(item) {
            closeSuggestions();
            const label = `${item.nome}${item.uf ? ` - ${item.uf}` : ''}`;
            field.value = label;
            showSelectedMunicipality(label);
            const bbox = Array.isArray(item.bbox) ? item.bbox.map(Number) : [];
            if (bbox.length === 4 && bbox.every(Number.isFinite) && bbox[0] < bbox[2] && bbox[1] < bbox[3]) {
                const map = window.CONFRONTA_MAP_CONTEXT?.map;
                if (map && typeof map.fitBounds === 'function') map.fitBounds([[bbox[1], bbox[0]], [bbox[3], bbox[2]]], { padding: [70, 70], maxZoom: 13 });
            }
        }

        function renderMunicipalities(items) {
            suggestions.replaceChildren();
            if (!items.length) {
                const empty = document.createElement('div'); empty.className = 'municipality-suggestion-empty'; empty.textContent = 'Nenhum município encontrado.';
                suggestions.appendChild(empty);
            } else items.slice(0, 10).forEach((item) => {
                const option = document.createElement('button');
                option.type = 'button'; option.className = 'municipality-suggestion'; option.setAttribute('role', 'option');
                const name = document.createElement('strong'); name.textContent = item.nome;
                const state = document.createElement('small'); state.textContent = item.uf || '';
                option.append(name, state);
                option.addEventListener('click', () => selectMunicipality(item));
                suggestions.appendChild(option);
            });
            suggestions.hidden = false;
        }

        field?.addEventListener('input', () => {
            window.clearTimeout(debounceTimer);
            const resolved = resolveInput(field.value);
            if (resolved.type !== 'municipality') { closeSuggestions(); showFeedback(resolved.type === 'car' ? 'CAR identificado' : resolved.type === 'coordinate' ? 'Coordenada identificada' : ''); return; }
            showFeedback('Município');
            if (resolved.value.length < 2) { closeSuggestions(); return; }
            debounceTimer = window.setTimeout(async () => {
                try { renderMunicipalities(await loadMunicipalities(resolved.value)); }
                catch (error) { if (error.name !== 'AbortError') showFeedback(error.message); }
            }, 300);
        });

        universalSearch.addEventListener('submit', async (event) => {
            window.clearTimeout(debounceTimer);
            const resolved = resolveInput(field?.value || '');
            if (resolved.type === 'empty') { event.preventDefault(); field?.focus(); return; }
            if (resolved.type === 'invalid-car') { event.preventDefault(); closeSuggestions(); showFeedback('CAR inválido. Confira UF, município e identificador.'); return; }
            if (resolved.type === 'invalid-coordinate') { event.preventDefault(); closeSuggestions(); showFeedback('Coordenada inválida: latitude −90 a 90 e longitude −180 a 180.'); return; }
            if (resolved.type === 'car') { field.value = resolved.value; closeSuggestions(); showFeedback('CAR identificado'); return; }
            if (resolved.type === 'coordinate') {
                event.preventDefault(); closeSuggestions(); showFeedback('Coordenada identificada');
                const form = document.createElement('form'); form.method = 'post';
                form.action = config?.dataset.coordinateQueryUrl || universalSearch.action;
                const token = universalSearch.querySelector('input[name="csrfmiddlewaretoken"]')?.value;
                [['csrfmiddlewaretoken', token], ['latitude', resolved.latitude], ['longitude', resolved.longitude]].forEach(([name, value]) => {
                    const input = document.createElement('input'); input.type = 'hidden'; input.name = name; input.value = value ?? ''; form.appendChild(input);
                });
                document.body.appendChild(form); form.submit(); return;
            }
            event.preventDefault();
            try {
                const matches = await loadMunicipalities(resolved.value);
                renderMunicipalities(matches);
                showFeedback(matches.length ? 'Escolha um município para enquadrar o mapa.' : 'Nenhum município encontrado.');
            } catch (error) { if (error.name !== 'AbortError') showFeedback(error.message); }
        });
        document.addEventListener('click', (event) => { if (!universalSearch.contains(event.target) && !suggestions.contains(event.target)) closeSuggestions(); });
        document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeSuggestions(); });
        document.getElementById('open-new-query')?.addEventListener('click', closeSuggestions);
    }

    // MÓDULO 2 — a navegação permanece na mesma instância do mapa.
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
            if (selectCarMessage) selectCarMessage.textContent = button.dataset.emptyMessage || 'Use a busca do CAR ou o botão Nova consulta.';
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
    const railCreditMap = document.getElementById('rail-credit-map');
    const railGlebas = document.getElementById('rail-glebas');
    const layerDrawer = document.getElementById('map-layer-drawer');
    const layerDrawerClose = document.getElementById('close-layer-drawer');
    const toolDrawer = document.getElementById('territorial-side-panel');
    const toolDrawerHeader = toolDrawer?.querySelector('.territorial-tool-drawer-head');
    const toolDrawerClose = document.getElementById('territorial-tool-close');
    const toolKicker = document.getElementById('territorial-tool-kicker');
    const toolTitle = document.getElementById('territorial-tool-title');
    const creditMapNotice = document.getElementById('credit-map-notice');
    const creditMapPrintButton = document.getElementById('credit-map-print-button');
    const creditMapPrintSummary = document.getElementById('credit-map-print-summary');
    let creditMapLayerState = null;
    const drawerAnalysisCounts = document.getElementById('drawer-analysis-counts');
    const analysisItems = Array.from(document.querySelectorAll('[data-analysis-item]'));

    function syncAnalysisSummary() {
        const counts = { alert: 0, territorial: 0, credit: 0 };
        analysisItems.forEach((item) => {
            if (['alerta', 'atencao'].includes(item.dataset.state)) counts[item.dataset.analysisItem] += 1;
        });
        const alertText = `${counts.alert} alerta${counts.alert === 1 ? '' : 's'}`;
        const header = document.getElementById('drawer-analysis-counts');
        const summary = document.getElementById('map-analysis-summary');
        const detail = document.getElementById('map-analysis-summary-detail');
        if (header) header.textContent = `${alertText} • ${counts.territorial} interferências • ${counts.credit} SICOR`;
        if (summary) summary.textContent = `${counts.alert} alertas · ${counts.territorial} interferência${counts.territorial === 1 ? '' : 's'} · ${counts.credit} SICOR`;
        if (detail) detail.textContent = 'Ocorrências, sobreposições e crédito rural';
        document.querySelectorAll('[data-analysis-group]').forEach((group) => {
            const rows = Array.from(group.querySelectorAll('[data-analysis-item]'));
            rows.sort((a, b) => Number(['alerta', 'atencao'].includes(b.dataset.state)) - Number(['alerta', 'atencao'].includes(a.dataset.state)));
            rows.forEach((row) => group.querySelector('.property-alert-list')?.appendChild(row));
        });
    }
    syncAnalysisSummary();

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
        if (creditMapLayerState) toggleCreditMapMode();
        const willOpen = layerDrawer.hidden;
        closeToolDrawer();
        layerDrawer.hidden = !willOpen;
        setRailPressed(railLayers, willOpen);
    }

    function closeToolDrawer() {
        if (!toolDrawer) return;
        toolDrawer.hidden = true;
        toolDrawer.classList.remove('is-open', 'is-glebas-mode', 'is-alerts-mode');
        toolDrawerHeader?.classList.remove('map-layer-drawer-head');
        setRailPressed(railCreditMap, Boolean(creditMapLayerState));
        setRailPressed(railGlebas, false);
        setRailPressed(railAlerts, false);
        if (context && context.map) window.setTimeout(() => context.map.invalidateSize(), 40);
    }

    function openToolDrawer(mode) {
        if (!toolDrawer) return;
        if (creditMapLayerState && mode !== 'glebas') toggleCreditMapMode();
        closeLayerDrawer();
        toolDrawer.hidden = false;
        toolDrawer.classList.add('is-open');
        toolDrawer.classList.toggle('is-glebas-mode', mode === 'glebas');
        toolDrawer.classList.toggle('is-alerts-mode', mode === 'alerts');
        toolDrawerHeader?.classList.toggle('map-layer-drawer-head', mode === 'glebas');
        if (drawerAnalysisCounts) {
            drawerAnalysisCounts.hidden = mode !== 'alerts';
            drawerAnalysisCounts.textContent = analysisCountText();
        }

        if (mode === 'glebas') {
            setTerritorialView('map');
            setSideTab('glebas');
            if (toolKicker) toolKicker.textContent = 'POLÍGONOS';
            if (toolTitle) toolTitle.textContent = 'Polígonos desenhados para o crédito';
            setRailPressed(railGlebas, true);
            setRailPressed(railCreditMap, Boolean(creditMapLayerState));
            setRailPressed(railAlerts, false);
        } else if (mode === 'alerts') {
            setTerritorialView('map');
            setSideTab('layers');
            if (toolKicker) toolKicker.textContent = 'ANÁLISE DO IMÓVEL';
            if (toolTitle) toolTitle.textContent = context?.consulta?.imovel?.cod_imovel || 'Análise territorial';
            setRailPressed(railAlerts, true);
            setRailPressed(railCreditMap, false);
            setRailPressed(railGlebas, false);
            window.setTimeout(() => {
                const target = toolDrawer.querySelector('.alert-title') || toolDrawer.querySelector('.property-alert-list');
                if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 80);
        }
        if (context && context.map) window.setTimeout(() => context.map.invalidateSize(), 40);
    }

    function analysisCountText() {
        const count = (type) => analysisItems.filter((item) =>
            item.dataset.analysisItem === type && ['alerta', 'atencao'].includes(item.dataset.state)
        ).length;
        return `${count('alert')} alertas • ${count('territorial')} interferências • ${count('credit')} SICOR`;
    }

    function syncLayerEye(key, visible) {
        document.querySelectorAll(`[data-layer-eye="${CSS.escape(key)}"]`).forEach((button) => {
            button.classList.toggle('is-active', Boolean(visible));
            button.setAttribute('aria-pressed', visible ? 'true' : 'false');
        });
    }

    function toggleCreditMapMode() {
        if (!context || !context.map || !context.consulta?.imovel?.geometry) {
            if (creditMapNotice) {
                creditMapNotice.textContent = 'Selecione um CAR para visualizar o Mapa Crédito.';
                creditMapNotice.hidden = false;
            }
            return;
        }
        if (creditMapLayerState) {
            creditMapLayerState.forEach(({ key, visible }) => {
                context.setLayerVisible(key, visible);
                syncLayerEye(key, visible);
            });
            creditMapLayerState = null;
            document.body.classList.remove('is-credit-map-mode');
            if (creditMapPrintButton) creditMapPrintButton.hidden = true;
            setRailPressed(railCreditMap, false);
            window.dispatchEvent(new CustomEvent('confronta:credit-map-mode', { detail: { active: false } }));
            if (creditMapNotice) creditMapNotice.hidden = true;
            window.setTimeout(() => context.map.invalidateSize({ pan: false }), 60);
            return;
        }
        const controls = Array.from(document.querySelectorAll('#map-layer-drawer [data-layer-eye]'));
        creditMapLayerState = controls.map((button) => ({
            key: button.dataset.layerEye,
            visible: button.getAttribute('aria-pressed') === 'true'
        })).filter((item) => item.key);

        const internalKeys = new Set(Object.entries(context.consulta.camadas || {})
            .filter(([, layerData]) => layerData?.disponivel && Array.isArray(layerData.features) && layerData.features.length)
            .map(([key]) => key));
        const creditVisibleKeys = new Set(['perimetro', 'glebas_usuario', ...internalKeys]);
        creditMapLayerState.forEach(({ key }) => {
            const keepInCreditMap = creditVisibleKeys.has(key)
                && (key !== 'perimetro' || Boolean(context.perimeter));
            context.setLayerVisible(key, keepInCreditMap);
            syncLayerEye(key, keepInCreditMap);
        });

        document.body.classList.add('is-credit-map-mode');
        if (creditMapPrintButton) creditMapPrintButton.hidden = false;
        setRailPressed(railCreditMap, true);
        if (creditMapNotice) {
            creditMapNotice.textContent = (window.CONFRONTA_GLEBAS_SNAPSHOT || []).length
                ? '' : 'Nenhuma gleba de crédito desenhada.';
            creditMapNotice.hidden = !creditMapNotice.textContent;
        }
        window.dispatchEvent(new CustomEvent('confronta:credit-map-mode', { detail: { active: true } }));
        openToolDrawer('glebas');
        window.setTimeout(() => {
            context.map.invalidateSize({ pan: false });
            window.dispatchEvent(new CustomEvent('confronta:credit-map-fit'));
        }, 100);
    }

    if (creditMapPrintButton) {
        creditMapPrintButton.addEventListener('click', () => {
            if (!creditMapLayerState) return;
            const check = { blockedReason: '' };
            window.dispatchEvent(new CustomEvent('confronta:credit-map-print-check', { detail: check }));
            if (check.blockedReason) {
                window.alert(check.blockedReason);
                return;
            }

            const printMap = context.map;
            const previousView = { center: printMap.getCenter(), zoom: printMap.getZoom() };
            const internalKeys = Object.keys(context.consulta.camadas || {}).filter((key) => key !== 'perimetro');
            const visibility = internalKeys.map((key) => ({
                key,
                visible: Boolean(context.layers[key] && printMap.hasLayer(context.layers[key]))
            }));
            let finished = false;
            const finishPrint = () => {
                if (finished) return;
                finished = true;
                document.body.classList.remove('is-credit-map-printing');
                window.removeEventListener('afterprint', finishPrint);
                visibility.forEach(({ key, visible }) => context.setLayerVisible(key, visible));
                requestAnimationFrame(() => {
                    printMap.invalidateSize({ pan: false });
                    printMap.setView(previousView.center, previousView.zoom, { animate: false });
                });
            };
            window.addEventListener('afterprint', finishPrint, { once: true });
            document.body.classList.add('is-credit-map-printing');
            visibility.forEach(({ key }) => context.setLayerVisible(key, false));

            const waitFor = (eventName, timeout = 500) => new Promise((resolve) => {
                let timer;
                const done = () => {
                    window.clearTimeout(timer);
                    printMap.off(eventName, done);
                    resolve();
                };
                printMap.once(eventName, done);
                timer = window.setTimeout(done, timeout);
            });
            const waitForTiles = () => new Promise((resolve) => {
                const tileLayers = [];
                printMap.eachLayer((layer) => {
                    const loading = typeof layer.isLoading === 'function' ? layer.isLoading() : Boolean(layer._loading);
                    if (layer instanceof L.TileLayer && loading) tileLayers.push(layer);
                });
                if (!tileLayers.length) { resolve(); return; }
                let remaining = tileLayers.length;
                let timer;
                const done = () => {
                    tileLayers.forEach((layer) => layer.off('load', onLoad));
                    window.clearTimeout(timer);
                    resolve();
                };
                const onLoad = () => { if (--remaining <= 0) done(); };
                tileLayers.forEach((layer) => layer.once('load', onLoad));
                timer = window.setTimeout(done, 1200);
            });

            (async () => {
                await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                printMap.invalidateSize({ pan: false });
                await new Promise((resolve) => requestAnimationFrame(resolve));
                const fitRequest = { bounds: null };
                window.dispatchEvent(new CustomEvent('confronta:credit-map-print-bounds', { detail: fitRequest }));
                const bounds = fitRequest.bounds?.isValid?.() ? fitRequest.bounds : context.perimeter?.getBounds?.();
                if (bounds?.isValid?.()) {
                    const moving = waitFor('moveend');
                    printMap.fitBounds(bounds, {
                        padding: [28, 28],
                        maxZoom: context.maxNativeZoom || 17,
                        animate: false
                    });
                    await moving;
                }
                await new Promise((resolve) => requestAnimationFrame(resolve));
                await waitForTiles();
                await new Promise((resolve) => requestAnimationFrame(resolve));
                window.print();
            })().catch(() => finishPrint());
        });
    }

    window.addEventListener('beforeprint', () => {
        if (document.body.classList.contains('is-credit-map-printing') && context?.map) {
            context.map.invalidateSize({ pan: false });
        }
    });

    if (layerDrawer) {
        layerDrawer.addEventListener('click', (event) => {
            const button = event.target.closest('button[data-layer-eye]');
            if (!button || !layerDrawer.contains(button)) return;
            const key = button.dataset.layerEye;
            if (!key || !context || typeof context.setLayerVisible !== 'function') return;
            const currentlyVisible = button.getAttribute('aria-pressed') === 'true';
            const nextVisible = !currentlyVisible;
            context.setLayerVisible(key, nextVisible);
            syncLayerEye(key, nextVisible);
            const layerLabel = button.closest('.layer-subitem')?.querySelector('.layer-subitem-label')?.textContent
                || button.querySelector('.map-layer-name')?.textContent
                || button.getAttribute('aria-label')
                || 'camada';
            button.setAttribute('aria-label', `${nextVisible ? 'Ocultar' : 'Exibir'} ${layerLabel}`);
        });
    }

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

    if (layerDrawer && context?.consulta?.imovel?.geometry && document.getElementById('app-config')?.dataset.openLayersOnCarLoad === 'true' && !creditMapLayerState) {
        closeToolDrawer();
        layerDrawer.hidden = false;
        setRailPressed(railLayers, true);
    }

    if (railFitCar) {
        railFitCar.addEventListener('click', () => {
            if (creditMapLayerState) toggleCreditMapMode();
            closeLayerDrawer();
            closeToolDrawer();
            if (context && typeof context.fitCar === 'function') context.fitCar();
        });
    }

    if (railCreditMap) {
        railCreditMap.addEventListener('click', () => {
            closeLayerDrawer();
            closeToolDrawer();
            toggleCreditMapMode();
        });
    }

    if (railGlebas) {
        railGlebas.addEventListener('click', () => {
            if (toolDrawer && !toolDrawer.hidden && toolDrawer.classList.contains('is-glebas-mode')) closeToolDrawer();
            else openToolDrawer('glebas');
        });
    }

    if (toolDrawerClose) toolDrawerClose.addEventListener('click', closeToolDrawer);


    window.addEventListener('confronta:glebas-updated', (event) => {
        const items = event.detail && event.detail.items;
        if (creditMapLayerState && creditMapNotice) {
            creditMapNotice.textContent = Array.isArray(items) && items.length ? '' : 'Nenhuma gleba de crédito desenhada.';
            creditMapNotice.hidden = !creditMapNotice.textContent;
        }
    });
    if (window.CONFRONTA_GLEBAS_SNAPSHOT && creditMapLayerState && creditMapNotice) {
        creditMapNotice.textContent = window.CONFRONTA_GLEBAS_SNAPSHOT.length ? '' : 'Nenhuma gleba de crédito desenhada.';
        creditMapNotice.hidden = !creditMapNotice.textContent;
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
