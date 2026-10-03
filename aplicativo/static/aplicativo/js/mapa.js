(function () {
    'use strict';

    const mapElement = document.getElementById('map');
    if (!mapElement || typeof L === 'undefined') return;

    // MÓDULO 2 — v0.3.6
    // Base híbrida: imagem de satélite + referências cartográficas.
    // maxNativeZoom limita as requisições ao nível seguro observado do serviço;
    // maxZoom permite aproximação adicional apenas por reamostragem do último tile,
    // evitando solicitar níveis sem imagem e exibir “sem mapa”.
    const MAX_SATELLITE_NATIVE_ZOOM = 17;
    const MAX_SATELLITE_ZOOM = 19;
    const fullscreenTarget = mapElement.closest('.client-shell') || mapElement;

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

    const fullscreenControl = L.control({ position: 'topleft' });
    let fullscreenButton = null;
    fullscreenControl.onAdd = function () {
        const button = L.DomUtil.create('button', 'leaflet-control-fullscreen', this._container);
        button.type = 'button';
        button.title = 'Tela cheia do mapa';
        button.setAttribute('aria-label', 'Colocar mapa em tela cheia');
        button.textContent = '⛶';
        fullscreenButton = button;
        L.DomEvent.disableClickPropagation(button);
        L.DomEvent.on(button, 'click', async function () {
            try {
                if (document.fullscreenElement === fullscreenTarget) await document.exitFullscreen();
                else if (!document.fullscreenElement && fullscreenTarget.requestFullscreen) await fullscreenTarget.requestFullscreen();
            } catch (error) {
                // A API pode ser bloqueada pelo navegador; o mapa permanece utilizável.
            }
        });
        return button;
    };
    fullscreenControl.addTo(map);

    const myLocationControl = L.control({ position: 'topright' });
    let locationNotice = null;
    let locationNoticeTimer = null;
    myLocationControl.onAdd = function () {
        const container = L.DomUtil.create('div', 'leaflet-bar confronta-location-control');
        const button = L.DomUtil.create('button', 'confronta-location-button', container);
        button.type = 'button';
        button.title = 'Minha localização';
        button.setAttribute('aria-label', 'Minha localização');
        button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>';
        L.DomEvent.disableClickPropagation(container);
        L.DomEvent.disableScrollPropagation(container);
        L.DomEvent.on(button, 'click', requestUserLocation);
        return container;
    };
    myLocationControl.addTo(map);

    const locationNoticeControl = L.control({ position: 'bottomleft' });
    locationNoticeControl.onAdd = function () {
        locationNotice = L.DomUtil.create('div', 'confronta-location-notice');
        locationNotice.setAttribute('role', 'status');
        locationNotice.setAttribute('aria-live', 'polite');
        locationNotice.hidden = true;
        return locationNotice;
    };
    locationNoticeControl.addTo(map);

    function showLocationMessage(message, timeout) {
        if (!locationNotice) return;
        window.clearTimeout(locationNoticeTimer);
        locationNotice.textContent = message;
        locationNotice.hidden = false;
        if (timeout) {
            locationNoticeTimer = window.setTimeout(() => {
                if (locationNotice) locationNotice.hidden = true;
            }, timeout);
        }
    }

    function showLocationUnavailable() {
        showLocationMessage('Não foi possível acessar sua localização. Navegue pelo mapa normalmente.', 5000);
    }

    function hideLocationMessage() {
        window.clearTimeout(locationNoticeTimer);
        if (locationNotice) locationNotice.hidden = true;
    }

    function requestUserLocation() {
        showLocationMessage('Localizando sua região...');
        if (!navigator.geolocation || typeof navigator.geolocation.getCurrentPosition !== 'function') {
            showLocationUnavailable();
            return;
        }
        navigator.geolocation.getCurrentPosition(
            (position) => {
                const latitude = position && position.coords && position.coords.latitude;
                const longitude = position && position.coords && position.coords.longitude;
                if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
                    showLocationUnavailable();
                    return;
                }
                map.setView([latitude, longitude], 13);
                hideLocationMessage();
            },
            showLocationUnavailable,
            { enableHighAccuracy: false, timeout: 10000, maximumAge: 30000 }
        );
    }

    let automaticLocationRequested = false;
    function requestLocationAutomatically() {
        // A consulta, CAR, geometria ou resultado carregado sempre tem prioridade.
        const activeCar = Boolean(configElement && configElement.dataset.car);
        if (automaticLocationRequested || rawData || activeCar) return;
        automaticLocationRequested = true;
        requestUserLocation();
    }

    document.addEventListener('fullscreenchange', function () {
        const active = document.fullscreenElement === fullscreenTarget;
        if (fullscreenButton) {
            fullscreenButton.textContent = active ? '⤢' : '⛶';
            fullscreenButton.title = active ? 'Sair da tela cheia' : 'Tela cheia do mapa';
            fullscreenButton.setAttribute('aria-label', active ? 'Sair da tela cheia do mapa' : 'Colocar mapa em tela cheia');
            fullscreenButton.classList.toggle('is-fullscreen', active);
        }
        window.setTimeout(() => map.invalidateSize(), 80);
    });

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

    // Referências cartográficas sobre o satélite.
    // Ficam acima da imagem e abaixo das camadas GIS/desenhos do usuário.
    // pointerEvents desativado garante que a camada não bloqueie desenho,
    // edição, medição ou clique nas feições do CONFRONTA.
    const referencePane = map.getPane('referenceLabelsPane') || map.createPane('referenceLabelsPane');
    referencePane.style.zIndex = '250';
    referencePane.style.pointerEvents = 'none';

    const referenceLabels = L.tileLayer(
        'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
        {
            minZoom: 0,
            maxNativeZoom: 19,
            maxZoom: MAX_SATELLITE_ZOOM,
            pane: 'referenceLabelsPane',
            updateWhenIdle: true,
            keepBuffer: 3,
            opacity: 1,
            attribution: 'Referências &copy; Esri, HERE, Garmin e comunidade GIS'
        }
    ).addTo(map);

    referenceLabels.on('tileerror', function (event) {
        console.warn(
            'CONFRONTA: falha ao carregar nomes e referências cartográficas.',
            event && event.coords ? event.coords : event
        );
    });

    L.control.layers(
        { 'Satélite': satellite },
        { 'Nomes e localidades': referenceLabels },
        { collapsed: true, position: 'topright' }
    ).addTo(map);


    // MAPA CLEAN V1.2 — mantém qualquer popup integralmente visível.
    // Considera também a faixa-resumo existente na parte inferior do mapa.
    function keepPopupInsideSafeArea(popup) {
        if (!popup || typeof popup.getElement !== 'function') return;

        const popupElement = popup.getElement();
        const mapContainer = map.getContainer();

        if (!popupElement || !mapContainer) return;

        const mapRect = mapContainer.getBoundingClientRect();
        const popupRect = popupElement.getBoundingClientRect();

        const safeTop = mapRect.top + 16;
        const safeLeft = mapRect.left + 18;
        const safeRight = mapRect.right - 18;
        const safeBottom = mapRect.bottom - 92;

        let shiftX = 0;
        let shiftY = 0;

        if (popupRect.left < safeLeft) {
            shiftX = safeLeft - popupRect.left;
        } else if (popupRect.right > safeRight) {
            shiftX = safeRight - popupRect.right;
        }

        if (popupRect.top < safeTop) {
            shiftY = safeTop - popupRect.top;
        } else if (popupRect.bottom > safeBottom) {
            shiftY = safeBottom - popupRect.bottom;
        }

        if (Math.abs(shiftX) > 1 || Math.abs(shiftY) > 1) {
            map.panBy(
                [-shiftX, -shiftY],
                {
                    animate: true,
                    duration: 0.18
                }
            );
        }
    }

    map.on('popupopen', function (event) {
        const popup = event && event.popup;
        window.setTimeout(() => keepPopupInsideSafeArea(popup), 40);
        window.setTimeout(() => keepPopupInsideSafeArea(popup), 180);
    });

    const rawData = document.getElementById('consulta-territorial-data');
    const configElement = document.getElementById('app-config');
    const canDraw = Boolean(configElement && configElement.dataset.canDraw === '1');
    const carCode = (configElement && configElement.dataset.car) || 'CAR';
    const layers = {};
    const sicorOccurrenceLayers = new Map();
    const overlapCarLayers = new Map();
    let perimeter = null;
    let selectedSicorFeatureLayer = null;
    let fullConservationUnitLayer = null;
    let fullConservationUnitKey = null;
    let fullConservationUnitRequestId = 0;
    let fullConservationUnitPendingButton = null;

    const LAYER_STYLES = {
        perimetro: { color: '#FFFFFF', weight: 3.2, fillOpacity: 0.01, opacity: 1, fillColor: '#FFFFFF' },
        cars_contextuais: { color: '#16A34A', fillColor: '#4ADE80', weight: 1.5, fillOpacity: 0.08, opacity: 0.92 },
        glebas_usuario: { color: '#1D4ED8', fillColor: '#3B82F6', weight: 1.8, fillOpacity: 0.34, opacity: 0.96 },
        app: { color: '#2b83cf', fillColor: '#2b83cf', weight: 1.8, fillOpacity: 0.22, opacity: 0.98 },
        reserva_legal: { color: '#65A30D', fillColor: '#84CC16', weight: 1.8, fillOpacity: 0.22, opacity: 1 },
        vegetacao_nativa: { color: '#15803D', fillColor: '#22C55E', weight: 1.8, fillOpacity: 0.20, opacity: 1 },
        area_consolidada: { color: '#D97706', fillColor: '#F59E0B', weight: 1.8, fillOpacity: 0.22, opacity: 0.98 },
        area_pousio: { color: '#b67a18', weight: 2.2, fillOpacity: 0.20, opacity: 0.97 },
        hidrografia: { color: '#0f8fd6', weight: 2.45, fillOpacity: 0.24, opacity: 0.98 },
        servidao_administrativa: { color: '#5f6b7a', weight: 2.15, fillOpacity: 0.18, opacity: 0.96 },
        uso_restrito: { color: '#99622a', weight: 2.2, fillOpacity: 0.22, opacity: 0.97 },
        ext_ibama: { color: '#B91C1C', fillColor: '#EF4444', weight: 1.8, fillOpacity: 0.28, opacity: 0.98 },
        ext_icmbio_embargo: { color: '#B91C1C', fillColor: '#EF4444', weight: 1.8, fillOpacity: 0.28, opacity: 0.99 },
        ext_prodes: { color: '#C2410C', fillColor: '#F97316', weight: 1.8, fillOpacity: 0.25, opacity: 0.99 },
        ext_prodes_desmatamento: { color: '#C2410C', fillColor: '#F97316', weight: 1.8, fillOpacity: 0.25, opacity: 0.99 },
        ext_prodes_queimada: { color: '#C2410C', fillColor: '#F97316', weight: 1.8, fillOpacity: 0.25, opacity: 0.99 },
        ext_assentamentos: { color: '#92400E', fillColor: '#D97706', weight: 1.8, fillOpacity: 0.19, opacity: 0.98 },
        ext_quilombolas: { color: '#9F1239', fillColor: '#BE123C', weight: 1.8, fillOpacity: 0.21, opacity: 0.99 },
        ext_funai: { color: '#6D28D9', fillColor: '#8B5CF6', weight: 1.8, fillOpacity: 0.21, opacity: 0.99 },
        ext_apa: { color: '#047857', fillColor: '#10B981', weight: 1.8, fillOpacity: 0.17, opacity: 0.98 },
        ext_sicor: { color: '#1D4ED8', fillColor: '#3B82F6', weight: 2, fillOpacity: 0.22, opacity: 0.98 },
        ext_outros_car: { color: '#E11D48', fillColor: '#F43F5E', weight: 1.8, fillOpacity: 0.28, opacity: 0.99 }
    };

    function normalizeHexColor(value, fallback) {
        const raw = String(value || fallback || '').trim().toUpperCase();
        return /^#[0-9A-F]{6}$/.test(raw) ? raw : String(fallback || '#0B7567').toUpperCase();
    }

    function styleForKey(key) {
        const base = LAYER_STYLES[key] || { color: '#4FA36A', fillColor: '#4FA36A', weight: 1.9, fillOpacity: 0.16, opacity: 0.92 };
        return { ...base };
    }

    // A legenda usa exatamente a mesma cor da camada desenhada no mapa.
    // LAYER_STYLES é a única fonte de verdade para a simbologia.
    function syncLayerSwatches() {
        document.querySelectorAll(
            '#map-layer-drawer [data-layer-eye]'
        ).forEach((button) => {
            const key = button.dataset.layerEye;
            const swatch = button.querySelector(
                '.map-layer-swatch'
            );

            if (!key || !swatch) return;

            const style = LAYER_STYLES[key];
            if (!style) return;

            const color = normalizeHexColor(
                style.color,
                '#FFFFFF'
            );

            swatch.style.backgroundColor = color;

            swatch.style.borderColor = (
                color === '#FFFFFF'
                    ? '#61747B'
                    : color
            );
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener(
            'DOMContentLoaded',
            syncLayerSwatches,
            { once: true }
        );
    } else {
        syncLayerSwatches();
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

    function applyStyleToFeatureLayer(featureLayer, layerKey) {
        if (!featureLayer || typeof featureLayer.setStyle !== 'function') return;
        const style = styleForKey(layerKey);
        if (typeof L !== 'undefined' && featureLayer instanceof L.CircleMarker) featureLayer.setStyle(pointStyleFromVectorStyle(style));
        else featureLayer.setStyle(style);
    }

    function sicorOccurrenceDescriptor(feature, index) {
        const props = feature?.properties || {};
        const ref = String(props.ref_bacen || '').trim();
        const order = String(props.nu_ordem || '').trim();
        const year = String(props.ano_sicor || props._ano_arquivo || props.ano_operacao || '').trim();
        const operationId = String(props.nu_identificador || '').trim();
        const identity = [ref, order, year, operationId].map((part) => part || '-').join('|');
        const fallback = !ref && !order && !year && !operationId
            ? `feature-${index}-${String(props.nu_indice || props.nu_indice_gleba || props.indice_gleba || '')}`
            : identity;
        return {
            key: `sicor:${fallback}`,
            ref: ref || 'Referência não informada',
            order,
            hasReference: Boolean(ref)
        };
    }

    function overlapCarIdentity(feature, index) {
        const props = feature?.properties || {};
        return String(props.cod_imovel || '').trim() || `car-feature-${index}`;
    }

    function distributeFeatureLayers(key, featureGroup) {
        if (!['ext_sicor', 'ext_outros_car'].includes(key)) return featureGroup;
        const destination = key === 'ext_sicor' ? sicorOccurrenceLayers : overlapCarLayers;
        const masterGroup = L.layerGroup();
        const featureLayers = [];
        featureGroup.eachLayer((featureLayer) => featureLayers.push(featureLayer));
        let index = 0;
        featureLayers.forEach((featureLayer) => {
            const feature = featureLayer.feature;
            const identity = key === 'ext_sicor'
                ? sicorOccurrenceDescriptor(feature, index)
                : { key: overlapCarIdentity(feature, index) };
            let subgroup = destination.get(identity.key);
            if (!subgroup) {
                subgroup = L.layerGroup();
                subgroup._confrontaMenuIdentity = identity;
                destination.set(identity.key, subgroup);
                masterGroup.addLayer(subgroup);
            }
            featureGroup.removeLayer(featureLayer);
            subgroup.addLayer(featureLayer);
            index += 1;
        });
        return masterGroup;
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

    function cleanPopupValue(value) {
        if (value === null || value === undefined || typeof value === 'object') return '';
        const text = String(value).trim();
        return ['null', 'none', 'undefined', 'nan'].includes(text.toLowerCase()) ? '' : text;
    }

    function popupDate(value) {
        const text = cleanPopupValue(value);
        if (!text) return '';
        const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
        if (iso) return `${iso[3]}/${iso[2]}/${iso[1]}`;
        const br = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(text);
        return br ? text : text;
    }

    function popupNumber(value, digits = 2) {
        const text = cleanPopupValue(value);
        if (!text) return '';
        const numeric = typeof value === 'number' ? value : Number(text.replace(',', '.'));
        return Number.isFinite(numeric)
            ? numeric.toLocaleString('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits })
            : text;
    }

    function popupArea(value) {
        const number = popupNumber(value);
        return number ? `${number} ha` : '';
    }

    function sourcePopupFields(feature, layerKey, layerData) {
        const props = feature?.properties || {};
        const aliases = {
            ext_ibama: [
                ['Número do embargo', props.numero_embargo || props.seq_tad], ['Situação', props.situacao],
                ['Imóvel', props.nome_imovel], ['Município / UF', [props.municipio, props.uf].filter(Boolean).join(' / ')],
                ['Data', popupDate(props.data_embargo)], ['Área embargada', popupArea(props.area_embargo_informada_ha || props.area_geometria_ha)],
                ['Descrição / infração', props.descricao_infracao || props.descricao_termo], ['Processo', props.processo],
                ['Fonte', 'IBAMA']
            ],
            ext_icmbio_embargo: [
                ['Número do embargo', props.numero_embargo], ['Situação', props.situacao], ['Data', popupDate(props.data_embargo)],
                ['Área embargada', popupArea(props.area_ha || props.area_geometria_ha)],
                ['Município / UF', [props.municipio, props.uf].filter(Boolean).join(' / ')],
                ['Unidade de Conservação', props.unidade_conservacao || props.nome_uc],
                ['Motivo / infração', props.motivo || props.descricao_infracao], ['Processo', props.processo], ['Fonte', 'ICMBio']
            ],
            ext_funai: [
                ['Terra Indígena', props.terrai_nom], ['Etnia / povo', props.etnia_nome],
                ['Município / UF', [props.municipio, props.uf_sigla].filter(Boolean).join(' / ')],
                ['Fase', props.fase_ti], ['Modalidade', props.modalidade],
                ['Superfície', popupNumber(props.superficie)], ['Área da geometria', popupArea(props.area_geometria_ha)],
                ['Atualização', popupDate(props.data_atualizacao)], ['Fonte', 'FUNAI']
            ],
            ext_quilombolas: [
                ['Território / comunidade', props.nome || props.identificacao],
                ['Município / UF', [props.municipio, props.uf].filter(Boolean).join(' / ')],
                ['Situação', props.situacao], ['Fase', props.fase],
                ['Área', popupArea(props.area_ha || props.area_geometria_ha)], ['Processo', props.processo], ['Fonte', 'INCRA']
            ],
            ext_apa: [
                ['Nome', props.nome_uc], ['Categoria de manejo', props.categoria_manejo || props.sigla_categoria],
                ['Grupo de manejo', props.grupo_manejo], ['Esfera', props.esfera], ['Órgão gestor', props.orgao_gestor || props.gerencia_regional],
                ['Município / UF', [props.municipio, props.uf].filter(Boolean).join(' / ')],
                ['Área', popupArea(props.area_ha || props.area_geometria_ha)], ['Situação', props.situacao],
                ['Ano de criação', props.ano_criacao], ['Ato de criação', props.ato_criacao], ['Plano de manejo', props.plano_manejo],
                ['Fonte', props.fonte_integrada || 'CNUC / ICMBio']
            ],
            ext_assentamentos: [
                ['Assentamento', props.nome], ['Código', props.codigo], ['Município / UF', [props.municipio, props.uf].filter(Boolean).join(' / ')],
                ['Área', popupArea(props.area_ha || props.area_calculada_ha || props.area_geometria_ha)],
                ['Situação', props.situacao], ['Tipo / modalidade', props.modalidade], ['Data de criação', popupDate(props.data_criacao)],
                ['Famílias', props.quantidade_familias || props.capacidade_familias], ['Fonte', 'INCRA']
            ],
            ext_prodes: [
                ['Ano', props.year], ['Classe', props.class_name || props.main_class], ['Tipo de ocorrência', props.tipo_prodes],
                ['Área', props.area_km != null ? popupArea(Number(String(props.area_km).replace(',', '.')) * 100) : popupArea(props.area_geometria_ha)],
                ['UF', props.state], ['Data da detecção', popupDate(props.image_date)], ['Fonte', 'INPE / PRODES']
            ]
        };
        if (aliases[layerKey]) return aliases[layerKey].filter(([, value]) => cleanPopupValue(value));

        if (layerKey === 'ext_outros_car') {
            return [
                ['Número do CAR', props.cod_imovel], ['Município / UF', [props.municipio, props.uf].filter(Boolean).join(' / ')],
                ['Área total', popupArea(props.area_total_ha)], ['Área de sobreposição', popupArea(props.area_sobreposta_ha)],
                ['Sobreposição do CAR consultado', props.percentual_car_consultado != null ? `${popupNumber(props.percentual_car_consultado)}%` : ''],
                ['Sobreposição deste CAR', props.percentual_outro_car != null ? `${popupNumber(props.percentual_outro_car)}%` : ''],
                ['Situação', props.situacao_car || props.condicao]
            ].filter(([, value]) => cleanPopupValue(value));
        }

        if (!layerKey.startsWith('ext_')) {
            const names = { tipo: 'Tipo da área', situacao: 'Situação', nome: 'Nome' };
            const rows = [];
            ['tipo', 'situacao', 'nome'].forEach((key) => {
                if (props[key] !== undefined && props[key] !== null && props[key] !== '') {
                    rows.push([names[key], props[key]]);
                }
            });
            const area = selectedAreaHa(feature, layerData);
            const propertyArea = Number(consulta?.imovel?.area_total_ha);
            if (area !== null && Number.isFinite(propertyArea) && propertyArea > 0) {
                rows.push(['Percentual do CAR', `${popupNumber((area / propertyArea) * 100)}%`]);
            }
            return rows.filter(([, value]) => cleanPopupValue(value));
        }

        return [];
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

    function downloadFeatureKml(feature, label, options = {}) {
        if (!feature || !feature.geometry) return;
        const geometry = geometryToKml(feature.geometry);
        if (!geometry) return;
        const safeLabel = String(label || 'area').replace(/[<>:&"']/g, ' ').trim() || 'area';
        const xmlLabel = escapeHtml(safeLabel);
        const extendedData = Object.entries(options.metadata || {})
            .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== '')
            .map(([name, value]) => `<Data name="${escapeHtml(name)}"><value>${escapeHtml(value)}</value></Data>`)
            .join('');
        const kml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>${xmlLabel}</name><Placemark><name>${xmlLabel}</name>${extendedData ? `<ExtendedData>${extendedData}</ExtendedData>` : ''}${geometry}</Placemark></Document></kml>`;
        const blob = new Blob([kml], { type: 'application/vnd.google-earth.kml+xml;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        const filename = options.filename
            ? String(options.filename).toLowerCase().replace(/[^a-z0-9áàâãéèêíïóôõöúçñ_-]+/gi, '_').replace(/^_+|_+$/g, '') || 'area'
            : safeLabel.toLowerCase().replace(/[^a-z0-9áàâãéèêíïóôõöúçñ_-]+/gi, '-').replace(/^-+|-+$/g, '') || 'area';
        anchor.download = `${filename}.kml`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 0);
    }

    function appendCardAction(card, text, className, onClick) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = className;
        button.textContent = text;
        button.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            onClick();
        });
        card.appendChild(button);
        return button;
    }

    function appendCarDetails(card, rows) {
        const details = document.createElement('div');
        details.className = 'cf-context-car-popup-details';
        card.classList.add('cf-map-popup');
        rows.forEach(([label, value]) => {
            const clean = cleanPopupValue(value);
            if (!clean) return;
            const row = document.createElement('div');
            row.className = 'cf-context-car-popup-row cf-map-popup-field';
            const name = document.createElement('strong');
            name.className = 'cf-map-popup-label';
            name.textContent = label;
            const content = document.createElement('span');
            content.className = 'cf-map-popup-value';
            content.textContent = clean;
            row.append(name, content);
            details.appendChild(row);
        });
        card.appendChild(details);
    }

    function startWorkingWithCar(code) {
        const form = document.querySelector('form.topbar-car-search');
        const input = form && form.querySelector('input[name="car"]');
        if (!form || !input || !code) return;
        input.value = code;
        form.requestSubmit();
    }

    function layerKmlUrl(key) {
        const template = configElement?.dataset.layerExportUrl;
        return template ? template.replace('CAMADA_PLACEHOLDER', encodeURIComponent(key)) : null;
    }

    function conservationUnitIdentity(feature) {
        const props = feature?.properties || {};
        const source = String(props._confronta_uc_source || '').toLowerCase();
        const field = String(props._confronta_uc_identifier_field || '');
        const identifier = String(props._confronta_uc_identifier || '').trim();
        if (!['cnuc', 'icmbio'].includes(source) || !field || !identifier) return null;
        return { source, field, identifier, key: `${source}:${field}:${identifier}` };
    }

    function removeFullConservationUnit() {
        fullConservationUnitRequestId += 1;
        if (fullConservationUnitPendingButton) {
            fullConservationUnitPendingButton.disabled = false;
            fullConservationUnitPendingButton.textContent = 'Visualizar UC completa';
            fullConservationUnitPendingButton = null;
        }
        if (fullConservationUnitLayer && map.hasLayer(fullConservationUnitLayer)) {
            map.removeLayer(fullConservationUnitLayer);
        }
        fullConservationUnitLayer = null;
        fullConservationUnitKey = null;
    }

    async function toggleFullConservationUnit(feature, layerData, button) {
        const identity = conservationUnitIdentity(feature);
        if (!identity) return;
        if (fullConservationUnitKey === identity.key && fullConservationUnitLayer) {
            removeFullConservationUnit();
            button.textContent = 'Visualizar UC completa';
            button.disabled = false;
            return;
        }

        removeFullConservationUnit();
        const requestId = ++fullConservationUnitRequestId;
        const endpoint = configElement?.dataset.ucFullGeometryUrl;
        if (!endpoint) return;
        button.disabled = true;
        button.textContent = 'Carregando UC completa…';
        fullConservationUnitPendingButton = button;

        try {
            const url = new URL(endpoint, window.location.href);
            url.searchParams.set('fonte', identity.source);
            url.searchParams.set('campo', identity.field);
            url.searchParams.set('identificador', identity.identifier);
            const response = await fetch(url.toString(), {
                method: 'GET',
                credentials: 'same-origin',
                headers: { Accept: 'application/json' }
            });
            const payload = await response.json();
            if (!response.ok || !payload?.feature?.geometry) {
                throw new Error(payload?.erro || 'Não foi possível carregar a UC completa.');
            }
            if (requestId !== fullConservationUnitRequestId) return;

            const fullFeature = {
                ...payload.feature,
                properties: { ...feature.properties, ...(payload.feature.properties || {}) }
            };
            const baseStyle = styleForKey('ext_apa');
            const fullStyle = {
                ...baseStyle,
                weight: Math.max(3, Number(baseStyle.weight || 2) + 0.7),
                fillOpacity: 0.10,
                dashArray: '6 4'
            };
            fullConservationUnitKey = identity.key;
            fullConservationUnitLayer = L.geoJSON(fullFeature, {
                pane: paneForLayerKey('ext_apa'),
                bubblingMouseEvents: false,
                interactive: true,
                style: () => fullStyle,
                onEachFeature: (completeFeature, featureLayer) => {
                    featureLayer.bindPopup(
                        () => buildTerritorialPopup(
                            completeFeature,
                            layerData?.label || 'Unidade de Conservação',
                            fullStyle.color,
                            'Unidade de Conservação',
                            layerData,
                            'ext_apa'
                        ),
                        {
                            maxWidth: 360,
                            minWidth: 270,
                            closeButton: true,
                            autoPan: true,
                            keepInView: true,
                            autoPanPaddingTopLeft: [24, 24],
                            autoPanPaddingBottomRight: [24, 92],
                            className: 'confronta-feature-leaflet-popup'
                        }
                    );
                }
            }).addTo(map);

            const bounds = fullConservationUnitLayer.getBounds();
            if (bounds && bounds.isValid()) {
                map.fitBounds(bounds, {
                    paddingTopLeft: [60, 40],
                    paddingBottomRight: [44, 112],
                    maxZoom: MAX_SATELLITE_ZOOM,
                    animate: false
                });
            }
            button.textContent = 'Ocultar UC completa';
            button.disabled = false;
            fullConservationUnitPendingButton = null;
        } catch (error) {
            if (requestId !== fullConservationUnitRequestId) return;
            button.textContent = 'Visualizar UC completa';
            button.disabled = false;
            fullConservationUnitPendingButton = null;
            showLocationMessage(error?.message || 'Não foi possível carregar a UC completa.', 5000);
        }
    }

    function buildTerritorialPopup(feature, label, color, kind, layerData, layerKey) {
        if (layerKey === 'ext_sicor') {
            const props = feature?.properties || {};
            const card = document.createElement('section');
            card.className = 'cf-map-popup cf-context-car-popup cf-layer-feature-popup';
            const heading = document.createElement('div');
            heading.className = 'cf-sicor-popup-heading';
            const title = document.createElement('strong');
            title.textContent = 'CRÉDITO RURAL SICOR';
            heading.appendChild(title);
            const year = props.ano_sicor || props._ano_arquivo || props.ano_operacao;
            if (year !== null && year !== undefined && String(year).trim() && !['none', 'null', 'undefined'].includes(String(year).trim().toLowerCase())) {
                const badge = document.createElement('span');
                badge.className = 'cf-sicor-popup-year';
                badge.textContent = String(year).trim();
                heading.appendChild(badge);
            }
            card.appendChild(heading);
            const clean = (value) => {
                if (value === null || value === undefined) return '';
                const text = String(value).trim();
                return ['none', 'null', 'undefined'].includes(text.toLowerCase()) ? '' : text;
            };
            const addField = (parent, label, value, modifier = '') => {
                const text = clean(value);
                if (!text) return;
                const field = document.createElement('div');
                field.className = `cf-sicor-popup-field${modifier ? ` ${modifier}` : ''}`;
                const caption = document.createElement('span');
                caption.textContent = label;
                const content = document.createElement('strong');
                content.textContent = text;
                field.append(caption, content);
                parent.appendChild(field);
            };
            const datePtBr = (value) => {
                const text = clean(value);
                if (!text) return null;
                const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
                return match ? `${match[3]}/${match[2]}/${match[1]}` : text;
            };
            const numberPtBr = (value, options = {}) => {
                if (!clean(value)) return null;
                const number = Number(String(value).trim().replace(',', '.'));
                if (!Number.isFinite(number)) return null;
                return new Intl.NumberFormat('pt-BR', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                    ...options
                }).format(number);
            };
            const rawCredit = clean(props.vl_parc_credito);
            const creditValue = rawCredit ? Number(rawCredit.replace(',', '.')) : null;
            const credit = creditValue !== null && Number.isFinite(creditValue)
                ? new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(creditValue)
                : null;
            addField(card, 'Instituição financeira', props.nome_instituicao || props.cnpj_if, 'is-wide');
            const segment = clean(props.segmento_instituicao);
            if (segment) {
                const segmentNode = document.createElement('small');
                segmentNode.className = 'cf-sicor-popup-segment';
                segmentNode.textContent = segment;
                card.appendChild(segmentNode);
            }
            addField(card, 'Programa', props.nome_programa || props.cd_programa, 'is-wide');
            const dates = document.createElement('div');
            dates.className = 'cf-sicor-popup-grid';
            addField(dates, 'Data de emissão', datePtBr(props.dt_emissao));
            addField(dates, 'Vencimento', datePtBr(props.dt_vencimento));
            if (dates.childElementCount) card.appendChild(dates);
            addField(card, 'Valor do crédito', credit, 'is-credit');
            const areas = document.createElement('div');
            areas.className = 'cf-sicor-popup-grid';
            const areaGleba = numberPtBr(props.area_gleba_sicor_ha);
            const areaFinanciada = numberPtBr(props.vl_area_financ);
            addField(areas, 'Área da gleba SICOR', areaGleba ? `${areaGleba} ha` : null);
            addField(areas, 'Área financiada', areaFinanciada ? `${areaFinanciada} ha` : null);
            if (areas.childElementCount) card.appendChild(areas);
            addField(card, 'REF BACEN', props.ref_bacen, 'is-reference');
            addField(card, 'Ordem da operação', props.nu_ordem);
            addField(card, 'Origem da geometria', props.origem_gleba_sicor);
            const ref = clean(props.ref_bacen) || 'sem_ref';
            const exportYear = clean(year) || 'sem_ano';
            const metadata = {
                'REF BACEN': clean(props.ref_bacen),
                'Ano': exportYear === 'sem_ano' ? '' : exportYear,
                'Ordem da operação': clean(props.nu_ordem),
                'Origem da geometria': clean(props.origem_gleba_sicor),
                'Instituição financeira': clean(props.nome_instituicao || props.cnpj_if),
                'Programa': clean(props.nome_programa || props.cd_programa),
                'Data de emissão': clean(datePtBr(props.dt_emissao)),
                'Vencimento': clean(datePtBr(props.dt_vencimento)),
                'Valor do crédito': clean(credit),
                'Área da gleba SICOR': areaGleba ? `${areaGleba} ha` : '',
                'Área financiada': areaFinanciada ? `${areaFinanciada} ha` : ''
            };
            appendCardAction(card, 'Baixar KML', 'cf-context-car-popup-action is-secondary cf-sicor-popup-download', () => {
                downloadFeatureKml(
                    feature,
                    `Crédito Rural SICOR - REF ${ref}`,
                    { filename: `sicor_${ref}_${exportYear}`, metadata }
                );
            });
            return card;
        }

        if (layerKey === 'ext_outros_car') {
            const props = feature?.properties || {};
            const card = document.createElement('section');
            card.className = 'cf-context-car-popup';
            const heading = document.createElement('div');
            heading.className = 'cf-context-car-popup-heading';
            heading.textContent = 'CAR sobreposto';
            card.appendChild(heading);
            const overlapArea = Number(props.area_sobreposta_ha);
            appendCarDetails(card, [
                ['Código CAR', props.cod_imovel],
                ['Município / UF', [props.municipio, props.uf].filter(Boolean).join(' / ')],
                ['Área total', popupArea(props.area_total_ha)],
                ['Situação do CAR', props.situacao_car || props.condicao],
                ['Área de sobreposição', Number.isFinite(overlapArea) && props.area_sobreposta_ha != null
                    ? `${popupNumber(overlapArea)} ha` : ''],
                ['Percentual do CAR consultado', props.percentual_car_consultado != null
                    ? `${popupNumber(props.percentual_car_consultado)}%` : (props.percentual_car != null ? `${popupNumber(props.percentual_car)}%` : '')],
                ['Percentual da feição', props.percentual_outro_car != null
                    ? `${popupNumber(props.percentual_outro_car)}%` : (props.percentual_fonte != null ? `${popupNumber(props.percentual_fonte)}%` : '')]
            ]);
            appendCardAction(card, 'Trabalhar no CAR', 'cf-context-car-popup-action', () => startWorkingWithCar(props.cod_imovel));
            appendCardAction(card, 'Baixar CAR em KML', 'cf-context-car-popup-action is-secondary', () => {
                const fullFeature = {
                    ...feature,
                    geometry: props._confronta_full_geometry || feature.geometry
                };
                downloadFeatureKml(fullFeature, props.cod_imovel || 'CAR-sobreposto');
            });
            return card;
        }

        if (layerKey !== 'perimetro' && !layerKey.startsWith('ext_')) {
            const selectedArea = selectedAreaHa(feature, layerData);
            const internalLayerNames = {
                app: 'APP',
                reserva_legal: 'Reserva Legal',
                vegetacao_nativa: 'Vegetação Nativa',
                area_consolidada: 'Área Consolidada',
                area_pousio: 'Área de Pousio',
                hidrografia: 'Hidrografia',
                servidao_administrativa: 'Servidão Administrativa',
                uso_restrito: 'Área de Uso Restrito'
            };
            const card = document.createElement('section');
            card.className = 'cf-map-popup cf-context-car-popup cf-layer-feature-popup';
            const heading = document.createElement('div');
            heading.className = 'cf-context-car-popup-heading';
            heading.textContent = internalLayerNames[layerKey] || String(label || '').trim() || humanizeKey(layerKey);
            card.appendChild(heading);
            const totalArea = Number(consulta?.imovel?.area_total_ha);
            appendCarDetails(card, [
                ['Código CAR', consulta?.imovel?.cod_imovel || carCode],
                ['Área total do CAR', consulta?.imovel?.area_total_ha !== null
                    && consulta?.imovel?.area_total_ha !== undefined && Number.isFinite(totalArea) ? popupArea(totalArea) : ''],
                ['Área da camada selecionada', selectedArea !== null
                    ? popupArea(selectedArea) : ''],
                ['Tipo da área', feature?.properties?.tipo],
                ['Situação', feature?.properties?.situacao],
                ['Percentual do CAR', selectedArea !== null && Number.isFinite(totalArea) && totalArea > 0
                    ? `${popupNumber((selectedArea / totalArea) * 100)}%` : '']
            ]);
            appendCardAction(card, 'Baixar camada em KML', 'cf-context-car-popup-action', () => {
                const url = layerKmlUrl(layerKey);
                if (url) window.location.assign(url);
            });
            appendCardAction(card, 'Baixar CAR em KML', 'cf-context-car-popup-action is-secondary', () => {
                const url = configElement?.dataset.carExportUrl;
                if (url) window.location.assign(url);
            });
            return card;
        }

        if (layerKey === 'perimetro') return buildWorkingCarPopup(consulta?.imovel || {}, consulta?.alertas);

        const root = document.createElement('section');
        root.className = 'cf-map-popup cf-feature-popup';
        const selectedColor = styleForKey(layerKey).color || color || '#0B7567';

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
        areaMetric.className = 'cf-feature-popup-metric cf-map-popup-field';
        const areaLabel = document.createElement('span');
        areaLabel.className = 'cf-map-popup-label';
        areaLabel.textContent = layerKey.startsWith('ext_') && feature?.properties?.area_sobreposta_ha != null
            ? 'Área de sobreposição' : 'Área da feição';
        const areaValue = document.createElement('strong');
        areaValue.className = 'cf-map-popup-value';
        const overlapArea = Number(feature?.properties?.area_sobreposta_ha);
        const area = layerKey.startsWith('ext_') && feature?.properties?.area_sobreposta_ha != null && Number.isFinite(overlapArea)
            ? overlapArea : selectedAreaHa(feature, layerData);
        areaValue.textContent = area === null ? 'Geometria disponível' : `${area.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ha`;
        areaMetric.append(areaLabel, areaValue);

        const sourceMetric = document.createElement('div');
        sourceMetric.className = 'cf-feature-popup-metric cf-map-popup-field';
        const sourceLabel = document.createElement('span');
        sourceLabel.className = 'cf-map-popup-label';
        sourceLabel.textContent = layerKey.startsWith('ext_') ? 'Fonte' : 'Tipo';
        const sourceValue = document.createElement('strong');
        sourceValue.className = 'cf-map-popup-value';
        sourceValue.textContent = layerKey.startsWith('ext_')
            ? (layerData?.label || label || 'Camada territorial')
            : (kind || 'Camada territorial');
        sourceMetric.append(sourceLabel, sourceValue);
        metrics.append(areaMetric, sourceMetric);
        body.appendChild(metrics);

        const carRow = document.createElement('div');
        carRow.className = 'cf-feature-popup-car-line cf-map-popup-field';
        const carLabel = document.createElement('span');
        carLabel.className = 'cf-map-popup-label';
        carLabel.textContent = 'CAR consultado';
        const carValue = document.createElement('strong');
        carValue.className = 'cf-map-popup-value';
        carValue.textContent = carCode;
        carValue.title = carCode;
        carRow.append(carLabel, carValue);
        body.appendChild(carRow);

        const props = sourcePopupFields(feature, layerKey, layerData);
        if (props.length) {
            const details = document.createElement('div');
            details.className = 'cf-feature-popup-details';
            props.forEach(([popupLabel, value]) => {
                const row = document.createElement('div');
                row.className = 'cf-map-popup-field';
                const name = document.createElement('span');
                name.className = 'cf-map-popup-label';
                name.textContent = popupLabel;
                const content = document.createElement('strong');
                content.className = 'cf-map-popup-value';
                content.textContent = cleanPopupValue(value);
                row.append(name, content);
                details.appendChild(row);
            });
            body.appendChild(details);
        }

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
        if (layerKey === 'ext_apa' && conservationUnitIdentity(feature)) {
            const completeUc = document.createElement('button');
            completeUc.type = 'button';
            completeUc.className = 'cf-uc-full-action';
            completeUc.textContent = fullConservationUnitKey === conservationUnitIdentity(feature).key
                ? 'Ocultar UC completa'
                : 'Visualizar UC completa';
            completeUc.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                toggleFullConservationUnit(feature, layerData, completeUc);
            });
            body.prepend(completeUc);
        }
        root.append(head, body);
        return root;
    }

    function buildWorkingCarPopup(imovel, alertas) {
        const popup = document.createElement('section');
        popup.className = 'cf-context-car-popup cf-working-car-popup';
        const heading = document.createElement('div');
        heading.className = 'cf-context-car-popup-heading';
        heading.textContent = 'CAR em trabalho';
        popup.appendChild(heading);

        const area = Number(imovel.area_total_ha);
        const alertCount = Number(alertas?.resumo?.quantidade) || 0;
        appendCarDetails(popup, [
            ['Código CAR', imovel.cod_imovel],
            ['Município / UF', [imovel.municipio, imovel.uf].filter(Boolean).join(' / ')],
            ['Área', imovel.area_total_ha !== null && imovel.area_total_ha !== undefined && Number.isFinite(area)
                ? popupArea(area) : ''],
            ['Situação do CAR', imovel.situacao_apresentacao],
            ['Alertas', alertCount > 0
            ? `${alertCount} identificado${alertCount === 1 ? '' : 's'}`
            : 'Nenhum alerta identificado']
        ]);
        appendCardAction(popup, 'Baixar CAR em KML', 'cf-context-car-popup-action', () => {
            const url = configElement?.dataset.carExportUrl;
            if (url) window.location.assign(url);
        });
        return popup;
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
        // O resultado analítico conserva a interseção; apenas a feição desenhada usa o imóvel inteiro.
        const displayFeatures = (key === 'ext_sicor'
            ? layerData.features.filter((feature) => {
                const geometry = feature && feature.geometry;
                return geometry && ['Polygon', 'MultiPolygon'].includes(geometry.type) && Array.isArray(geometry.coordinates);
            })
            : (key === 'ext_outros_car'
                ? layerData.features.map((feature) => ({
                ...feature,
                geometry: feature.properties?._confronta_full_geometry || feature.geometry
                }))
                : layerData.features)).filter((feature) => {
                    const geometry = feature && feature.geometry;
                    return geometry && Array.isArray(geometry.coordinates)
                        && ['Polygon', 'MultiPolygon', 'LineString', 'MultiLineString', 'Point', 'MultiPoint'].includes(geometry.type);
                });
        if (!displayFeatures.length) return;
        const group = L.geoJSON({ type: 'FeatureCollection', features: displayFeatures }, {
            pane: paneForLayerKey(key),
            bubblingMouseEvents: false,
            interactive: true,
            style: function () {
                return styleForKey(key);
            },
            pointToLayer: function (feature, latlng) {
                return L.circleMarker(latlng, pointStyleFromVectorStyle(styleForKey(key)));
            },
            onEachFeature: function (feature, layer) {
                applyStyleToFeatureLayer(layer, key);
                if (key === 'ext_sicor') {
                    const restoreSicorFeatureStyle = (featureLayer) => {
                        if (!featureLayer) return;
                        applyStyleToFeatureLayer(featureLayer, 'ext_sicor');
                    };
                    layer.on('click', function () {
                        if (selectedSicorFeatureLayer && selectedSicorFeatureLayer !== layer) {
                            restoreSicorFeatureStyle(selectedSicorFeatureLayer);
                        }
                        selectedSicorFeatureLayer = layer;
                        layer.setStyle({
                            ...styleForKey('ext_sicor'),
                            weight: 3,
                            opacity: 1,
                            fillOpacity: 0.35
                        });
                    });
                    layer.on('popupclose', function () {
                        if (selectedSicorFeatureLayer === layer) {
                            selectedSicorFeatureLayer = null;
                            restoreSicorFeatureStyle(layer);
                        }
                    });
                }
                layer.bindPopup(() => buildTerritorialPopup(
                    feature,
                    layerData.label || key,
                    styleForKey(key).color || '#0B7567',
                    key.startsWith('ext_') ? 'Base territorial externa' : 'Área da camada',
                    layerData,
                    key
                ), {
                    maxWidth: key === 'ext_sicor' ? 410 : 360,
                    minWidth: key === 'ext_sicor' ? 0 : 270,
                    closeButton: true,
                    autoPan: true,
            keepInView: true,
            autoPanPaddingTopLeft: [24, 24],
            autoPanPaddingBottomRight: [24, 92],
                    className: key === 'ext_sicor'
                        ? 'confronta-feature-leaflet-popup confronta-sicor-leaflet-popup'
                        : (!key.startsWith('ext_')
                            ? 'confronta-feature-leaflet-popup confronta-context-car-popup'
                            : 'confronta-feature-leaflet-popup')
                });
            }
        });
        const managedGroup = distributeFeatureLayers(key, group);
        layers[key] = managedGroup;
        applyLayerStyleObject(managedGroup, styleForKey(key));
        if (['ext_sicor', 'ext_outros_car'].includes(key)) {
            const occurrenceLayers = key === 'ext_sicor' ? sicorOccurrenceLayers : overlapCarLayers;
            if (visibleByDefault) occurrenceLayers.forEach((occurrenceLayer) => occurrenceLayer.addTo(map));
        } else if (visibleByDefault) {
            managedGroup.addTo(map);
        }
    }

    function ensureMapPane(name, zIndex, pointerEvents = 'auto') {
        const pane = map.getPane(name) || map.createPane(name);
        pane.style.zIndex = String(zIndex);
        pane.style.pointerEvents = pointerEvents;
        return pane;
    }

    const layerPanes = {
        confrontaGlebasPane: ensureMapPane('confrontaGlebasPane', 590),
        ibamaEmbargoPane: ensureMapPane('ibamaEmbargoPane', 560),
        icmbioEmbargoPane: ensureMapPane('icmbioEmbargoPane', 555),
        funaiPane: ensureMapPane('funaiPane', 550),
        quilombolasPane: ensureMapPane('quilombolasPane', 545),
        apaPane: ensureMapPane('apaPane', 540),
        assentamentosPane: ensureMapPane('assentamentosPane', 535),
        sicorGlebasPane: ensureMapPane('sicorGlebasPane', 530),
        prodesPane: ensureMapPane('prodesPane', 520),
        overlappingCarsPane: ensureMapPane('overlappingCarsPane', 510),
        workingCarLayersPane: ensureMapPane('workingCarLayersPane', 480),
        selectedCarPane: ensureMapPane('selectedCarPane', 470),
        carsContextuaisPane: ensureMapPane('carsContextuaisPane', 380),
        referenceLabelsPane: ensureMapPane('referenceLabelsPane', 250, 'none')
    };
    layerPanes.selectedCarPane.classList.add('confronta-selected-car-pane');

    function paneForLayerKey(key) {
        const paneByLayer = {
            ext_ibama: 'ibamaEmbargoPane',
            ext_icmbio_embargo: 'icmbioEmbargoPane',
            ext_funai: 'funaiPane',
            ext_quilombolas: 'quilombolasPane',
            ext_apa: 'apaPane',
            ext_assentamentos: 'assentamentosPane',
            ext_sicor: 'sicorGlebasPane',
            ext_prodes: 'prodesPane',
            ext_outros_car: 'overlappingCarsPane',
            perimetro: 'selectedCarPane'
        };
        return paneByLayer[key] || (key.startsWith('ext_') ? 'prodesPane' : 'workingCarLayersPane');
    }

    const autoVisibleExternalLayers = new Set([
        'ext_ibama', 'ext_icmbio_embargo', 'ext_funai', 'ext_quilombolas',
        'ext_apa', 'ext_assentamentos', 'ext_sicor', 'ext_prodes', 'ext_outros_car'
    ]);

    if (consulta && consulta.imovel && consulta.imovel.geometry) {
        perimeter = L.geoJSON({
            type: 'Feature',
            properties: { car: consulta.imovel.cod_imovel },
            geometry: consulta.imovel.geometry
        }, {
            pane: 'selectedCarPane',
            interactive: true,
            bubblingMouseEvents: false,
            style: styleForKey('perimetro'),
            onEachFeature: function (feature, layer) {
                applyStyleToFeatureLayer(layer, 'perimetro');
                layer.bindPopup(() => buildWorkingCarPopup(consulta.imovel, consulta.alertas), {
                    maxWidth: 330,
                    closeButton: true,
                    autoPan: true,
            keepInView: true,
            autoPanPaddingTopLeft: [24, 24],
            autoPanPaddingBottomRight: [24, 92],
                    className: 'confronta-context-car-popup confronta-working-car-popup'
                });
            }
        }).addTo(map);
        layers.perimetro = perimeter;

        Object.entries(consulta.camadas || {}).forEach(([key, layerData]) => {
            addGeoJsonLayer(key, layerData, true);
        });

        Object.entries(consulta.camadas_externas || {}).forEach(([key, layerData]) => {
            const layerKey = `ext_${key}`;
            addGeoJsonLayer(layerKey, layerData, autoVisibleExternalLayers.has(layerKey));
        });

        enquadrarCar();
    }

    const visibleLayers = new Map();
    if (perimeter) visibleLayers.set('perimetro', true);
    if (layers.ext_sicor) visibleLayers.set('ext_sicor', [...sicorOccurrenceLayers.values()].some((layer) => map.hasLayer(layer)));
    if (layers.ext_outros_car) visibleLayers.set('ext_outros_car', [...overlapCarLayers.values()].some((layer) => map.hasLayer(layer)));

    // Perímetros contextuais independentes das camadas da consulta atual.
    // O pane abaixo dos overlays preserva os desenhos e o CAR pesquisado em destaque.
    if (configElement && configElement.dataset.carsUrl) {
        const contextLayer = L.geoJSON(null, {
            pane: 'carsContextuaisPane',
            interactive: true,
            bubblingMouseEvents: false,
            style: styleForKey('cars_contextuais'),
            onEachFeature: function (feature, layer) {
                if (configElement.dataset.freeMode === 'true') {
                    layer.on('click', function () {
                        if (typeof window.CONFRONTA_SHOW_FREE_CAR_PAYWALL === 'function') {
                            window.CONFRONTA_SHOW_FREE_CAR_PAYWALL();
                        }
                    });
                    return;
                }

                const props = feature.properties || {};
                layer.bindPopup(function () {
                    const popup = document.createElement('section');
                    popup.className = 'cf-context-car-popup';
                    const heading = document.createElement('div');
                    heading.className = 'cf-context-car-popup-heading';
                    heading.textContent = 'CAR na área visível';
                    popup.appendChild(heading);
                    appendCarDetails(popup, [
                        ['Código CAR', props.cod_imovel],
                        ['Município / UF', [props.municipio, props.uf].filter(Boolean).join(' / ')],
                        ['Área', Number.isFinite(Number(props.area_total_ha)) && props.area_total_ha !== null
                            ? `${Number(props.area_total_ha).toLocaleString('pt-BR', { maximumFractionDigits: 2 })} ha` : 'Não informada'],
                        ['Situação do CAR', props.situacao_car]
                    ]);
                    appendCardAction(popup, 'Trabalhar no CAR', 'cf-context-car-popup-action', () => startWorkingWithCar(props.cod_imovel));
                    appendCardAction(popup, 'Baixar CAR em KML', 'cf-context-car-popup-action is-secondary', () => {
                        downloadFeatureKml(feature, props.cod_imovel || 'CAR-contextual');
                    });
                    return popup;
                }, {
                    className: 'confronta-context-car-popup',
                    maxWidth: 330,
                    autoPan: true,
                    autoPanPaddingTopLeft: [24, 24],
                    autoPanPaddingBottomRight: [24, 92]
                });
            }
        }).addTo(map);
        layers.cars_contextuais = contextLayer;
        visibleLayers.set('cars_contextuais', true);
        let timer = null;
        let controller = null;
        let lastKey = null;
        let pendingKey = null;
        let generation = 0;

        function viewport() {
            const zoom = map.getZoom();
            if (zoom < 12 || zoom > 19) return null;
            const bounds = map.getBounds();
            const values = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
            const maxSpan = 360 * 12 / (2 ** zoom);
            if (!values.every(Number.isFinite) || values[0] < -180 || values[2] > 180 ||
                values[1] < -90 || values[3] > 90 || values[0] >= values[2] || values[1] >= values[3] ||
                values[2] - values[0] > maxSpan ||
                values[3] - values[1] > maxSpan) return null;
            return { key: [zoom, ...values].join(','), zoom, values };
        }

        function abortPending() {
            generation += 1;
            window.clearTimeout(timer);
            timer = null;
            if (controller) controller.abort();
            controller = null;
            pendingKey = null;
        }

        function scheduleContext() {
            const current = viewport();
            if (!current) {
                abortPending();
                lastKey = null;
                contextLayer.clearLayers();
                return;
            }
            if (current.key === lastKey || current.key === pendingKey) return;
            abortPending();
            const version = generation;
            pendingKey = current.key;
            timer = window.setTimeout(async function () {
                timer = null;
                controller = new AbortController();
                const params = new URLSearchParams({
                    zoom: String(current.zoom), west: String(current.values[0]),
                    south: String(current.values[1]), east: String(current.values[2]),
                    north: String(current.values[3])
                });
                try {
                    const response = await fetch(`${configElement.dataset.carsUrl}?${params}`, {
                        signal: controller.signal, credentials: 'same-origin'
                    });
                    if (!response.ok) return;
                    const data = await response.json();
                    if (version !== generation || viewport()?.key !== current.key || !Array.isArray(data.features)) return;
                    contextLayer.clearLayers();
                    contextLayer.addData({ type: 'FeatureCollection', features: data.features });
                    if (!visibleLayers.get('cars_contextuais')) map.removeLayer(contextLayer);
                    lastKey = current.key;
                } catch (error) {
                    // A consulta contextual é opcional; o restante do mapa permanece disponível.
                } finally {
                    if (version === generation) {
                        controller = null;
                        pendingKey = null;
                    }
                }
            }, 250);
        }

        map.on('movestart zoomstart', abortPending);
        map.on('moveend zoomend', scheduleContext);
        scheduleContext();
    }

    function setLayerVisible(key, visible) {
        const sicorChild = sicorOccurrenceLayers.get(key);
        if (sicorChild) {
            if (visible && !map.hasLayer(sicorChild)) sicorChild.addTo(map);
            else if (!visible && map.hasLayer(sicorChild)) map.removeLayer(sicorChild);
            syncSicorMasterVisibility();
            return;
        }
        const overlapCarCode = key.startsWith('overlap-car:') ? key.slice('overlap-car:'.length) : key;
        const overlapChild = overlapCarLayers.get(overlapCarCode);
        if (overlapChild) {
            if (visible && !map.hasLayer(overlapChild)) overlapChild.addTo(map);
            else if (!visible && map.hasLayer(overlapChild)) map.removeLayer(overlapChild);
            syncOverlapMasterVisibility();
            return;
        }
        if (key === 'cars_contextuais') {
            const contextual = layers.cars_contextuais;
            visibleLayers.set(key, Boolean(visible));
            if (contextual) {
                if (visible && !map.hasLayer(contextual)) contextual.addTo(map);
                else if (!visible && map.hasLayer(contextual)) map.removeLayer(contextual);
            }
            return;
        }
        if (key === 'glebas_usuario') {
            window.dispatchEvent(new CustomEvent('confronta:glebas-visibility', { detail: { visible: Boolean(visible) } }));
            visibleLayers.set(key, Boolean(visible));
            return;
        }
        const layer = layers[key];
        if (!layer) return;
        if (key === 'ext_sicor' || key === 'ext_outros_car') {
            const sublayers = key === 'ext_sicor' ? sicorOccurrenceLayers : overlapCarLayers;
            sublayers.forEach((sublayer) => {
                if (visible && !map.hasLayer(sublayer)) sublayer.addTo(map);
                else if (!visible && map.hasLayer(sublayer)) map.removeLayer(sublayer);
            });
            visibleLayers.set(key, Boolean(visible) && sublayers.size > 0);
            if (key === 'ext_sicor') syncSicorMasterVisibility();
            else syncOverlapMasterVisibility();
            return;
        }
        if (visible) {
            if (!map.hasLayer(layer)) layer.addTo(map);
        } else if (map.hasLayer(layer)) {
            map.removeLayer(layer);
        }
        visibleLayers.set(key, Boolean(visible));
        document.querySelectorAll(`.layer-toggle[data-layer="${CSS.escape(key)}"]`).forEach((toggle) => {
            if (!toggle.disabled) toggle.checked = visible;
        });
    }

    function updateMasterButton(key, visibleCount, totalCount) {
        const button = document.querySelector(`#map-layer-drawer [data-layer-eye="${CSS.escape(key)}"]`);
        if (!button) return;
        const allVisible = totalCount > 0 && visibleCount === totalCount;
        const partiallyVisible = visibleCount > 0 && visibleCount < totalCount;
        button.classList.toggle('is-active', allVisible);
        button.classList.toggle('is-partial', partiallyVisible);
        button.setAttribute('aria-pressed', partiallyVisible ? 'mixed' : (allVisible ? 'true' : 'false'));
        button.title = partiallyVisible ? 'Algumas ocorrências estão visíveis' : (allVisible ? 'Ocultar todas' : 'Exibir todas');
    }

    function syncSicorMasterVisibility() {
        const visibleCount = [...sicorOccurrenceLayers.values()].filter((layer) => map.hasLayer(layer)).length;
        visibleLayers.set('ext_sicor', visibleCount > 0);
        updateMasterButton('ext_sicor', visibleCount, sicorOccurrenceLayers.size);
    }

    function syncOverlapMasterVisibility() {
        const visibleCount = [...overlapCarLayers.values()].filter((layer) => map.hasLayer(layer)).length;
        visibleLayers.set('ext_outros_car', visibleCount > 0);
        updateMasterButton('ext_outros_car', visibleCount, overlapCarLayers.size);
    }

    function makeSubitemButton(key, label, title) {
        const row = document.createElement('div');
        row.className = 'layer-subitem';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'layer-subitem-toggle is-active';
        button.dataset.layerEye = key;
        button.setAttribute('aria-pressed', 'true');
        button.setAttribute('aria-label', `Ocultar ${label}`);
        button.title = title || label;
        const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        icon.setAttribute('viewBox', '0 0 24 24');
        icon.setAttribute('aria-hidden', 'true');
        icon.innerHTML = '<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.7"/><path class="layer-eye-off-mark" d="M4 4l16 16"/>';
        const text = document.createElement('span');
        text.className = 'layer-subitem-label';
        text.textContent = label;
        if (title) text.title = title;
        button.appendChild(icon);
        row.append(button, text);
        return row;
    }

    function renderSicorOccurrencesMenu() {
        const master = document.querySelector('#map-layer-drawer [data-layer-eye="ext_sicor"]');
        if (!master) return;
        const subitems = document.createElement('div');
        subitems.className = 'layer-subitems';
        subitems.dataset.layerSubitemsFor = 'ext_sicor';
        const occurrences = [...sicorOccurrenceLayers.entries()];
        const referenceCounts = new Map();
        occurrences.forEach(([, layer]) => {
            const ref = layer._confrontaMenuIdentity.ref;
            referenceCounts.set(ref, (referenceCounts.get(ref) || 0) + 1);
        });
        occurrences.forEach(([key, layer]) => {
            const identity = layer._confrontaMenuIdentity;
            const label = `Ref. ${identity.ref}${referenceCounts.get(identity.ref) > 1 && identity.order ? ` · Ordem ${identity.order}` : ''}`;
            subitems.appendChild(makeSubitemButton(key, label));
        });
        master.insertAdjacentElement('afterend', subitems);
        subitems.hidden = !occurrences.length;
        syncSicorMasterVisibility();
    }

    function renderOverlapCarsMenu() {
        const master = document.querySelector('#map-layer-drawer [data-layer-eye="ext_outros_car"]');
        if (!master) return;
        const subitems = document.createElement('div');
        subitems.className = 'layer-subitems';
        subitems.dataset.layerSubitemsFor = 'ext_outros_car';
        overlapCarLayers.forEach((layer, carCodeValue) => {
            const label = layer._confrontaMenuIdentity.key;
            subitems.appendChild(makeSubitemButton(`overlap-car:${carCodeValue}`, label, label));
        });
        master.insertAdjacentElement('afterend', subitems);
        subitems.hidden = !overlapCarLayers.size;
        syncOverlapMasterVisibility();
    }

    document.querySelectorAll('.layer-toggle').forEach((toggle) => {
        toggle.addEventListener('change', function () {
            setLayerVisible(this.dataset.layer, this.checked);
        });
    });

    // A organização do menu é independente dos panes e da ordem de desenho.
    // Move os botões existentes, sem recriá-los nem alterar o estado/toggle.
    const layerMenuSource = document.querySelector('[data-map-layer-menu-source]');
    if (layerMenuSource) {
        const menuOrder = {
            car: ['cars_contextuais'],
            areas: ['perimetro', 'area_consolidada', 'reserva_legal', 'app', 'vegetacao_nativa', 'uso_restrito', 'servidao_administrativa', 'area_pousio', 'hidrografia'],
            glebas: ['glebas_usuario'],
            externas: ['ext_ibama', 'ext_icmbio_embargo', 'ext_funai', 'ext_quilombolas', 'ext_apa', 'ext_assentamentos', 'ext_sicor', 'ext_prodes', 'ext_outros_car']
        };
        Object.entries(menuOrder).forEach(([groupName, orderedKeys]) => {
            const group = document.querySelector(`[data-layer-menu-group="${groupName}"]`);
            if (!group) return;
            orderedKeys.forEach((key) => {
                const button = layerMenuSource.querySelector(`[data-layer-eye="${CSS.escape(key)}"]`);
                if (button) group.appendChild(button);
            });
            if (!group.querySelector('[data-layer-eye]')) group.hidden = true;
        });
        renderSicorOccurrencesMenu();
        renderOverlapCarsMenu();
    }

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
        referenceLabels,
        carCode,
        canDraw,
        maxNativeZoom: MAX_SATELLITE_NATIVE_ZOOM,
        maxZoom: MAX_SATELLITE_ZOOM,
        setLayerVisible,
        fitCar: enquadrarCar
    };

    // Consulta complementar por clique: mantém o CAR atual até uma ação
    // explícita em "Trabalhar neste CAR".
    map.on('click', async function (event) {
        const endpoint = configElement && configElement.dataset.carsPointUrl;
        if (!endpoint || configElement.dataset.canConsult !== 'true') return;
        const target = event?.originalEvent?.target;
        if (window.CONFRONTA_QUERY_DRAW_ACTIVE || window.CONFRONTA_MEASURE_ACTIVE ||
            document.body.classList.contains('is-drawing-gleba') ||
            document.body.classList.contains('is-measuring-distance') ||
            document.querySelector('#gleba-live-area-map.is-editing-gleba') ||
            document.querySelector('.leaflet-draw-tooltip, .leaflet-editing-icon, .leaflet-vertex-icon') ||
            (target && target.closest('.leaflet-control, .leaflet-popup, .leaflet-marker-icon, .leaflet-interactive, .leaflet-draw-tooltip'))) return;
        const point = event.latlng;
        const popup = L.popup({ maxWidth: 340, closeButton: true }).setLatLng(point).setContent('Localizando CARs…').openOn(map);
        try {
            const url = new URL(endpoint, window.location.origin);
            url.searchParams.set('latitude', point.lat.toFixed(7));
            url.searchParams.set('longitude', point.lng.toFixed(7));
            const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.erro || 'Não foi possível localizar CARs neste ponto.');
            if (!map.hasLayer(popup)) return;
            const content = document.createElement('div');
            const cars = Array.isArray(payload.resultados) ? payload.resultados : (Array.isArray(payload.cars) ? payload.cars : []);
            const title = document.createElement('strong');
            title.textContent = cars.length ? `${cars.length}${payload.truncada ? '+' : ''} CAR${cars.length === 1 ? '' : 's'} encontrado${cars.length === 1 ? '' : 's'} neste ponto` : 'Nenhum CAR encontrado neste ponto.';
            content.appendChild(title);
            if (payload.truncada) {
                const notice = document.createElement('p');
                notice.className = 'map-point-truncated';
                notice.textContent = 'Mais de 20 CARs encontrados neste ponto. Mostrando os primeiros 20.';
                content.appendChild(notice);
            }
            cars.forEach((car) => {
                const row = document.createElement('div');
                row.className = 'map-point-car-option';
                const details = document.createElement('p');
                const area = Number(car.area_total_ha);
                const areaText = car.area_total_ha !== null && car.area_total_ha !== '' && Number.isFinite(area)
                    ? `${area.toLocaleString('pt-BR', { maximumFractionDigits: 2 })} ha · ` : '';
                details.textContent = `CAR ${car.cod_imovel} · ${areaText}${car.municipio || ''}${car.uf ? ' - ' + car.uf : ''}`;
                const choose = document.createElement('button');
                choose.type = 'button';
                choose.className = 'map-point-car-work';
                choose.textContent = 'Trabalhar neste CAR';
                choose.addEventListener('click', () => {
                    const form = document.createElement('form');
                    form.method = 'post';
                    form.action = configElement.dataset.carQueryUrl;
                    const token = document.querySelector('input[name="csrfmiddlewaretoken"]')?.value;
                    if (token) {
                        const csrf = document.createElement('input'); csrf.type = 'hidden'; csrf.name = 'csrfmiddlewaretoken'; csrf.value = token; form.appendChild(csrf);
                    }
                    const field = document.createElement('input'); field.type = 'hidden'; field.name = 'car'; field.value = car.cod_imovel; form.appendChild(field);
                    document.body.appendChild(form); form.submit();
                });
                row.append(details, choose);
                content.appendChild(row);
            });
            popup.setContent(content);
        } catch (error) {
            if (map.hasLayer(popup)) popup.setContent(error.message || 'Não foi possível localizar CARs neste ponto.');
        }
    });

    requestLocationAutomatically();
    window.setTimeout(() => map.invalidateSize(), 80);
})();
