(function () {
    'use strict';

    const modal = document.getElementById('new-query-modal');
    const carView = modal && modal.querySelector('[data-query-view="car"]');
    const input = document.getElementById('modal-car');
    const dropzone = document.getElementById('car-image-ocr-dropzone');
    const filePicker = document.getElementById('car-image-ocr-file');
    const workspace = document.getElementById('car-image-ocr-workspace');
    const preview = document.getElementById('car-image-ocr-preview');
    const previewWrap = document.getElementById('car-image-ocr-preview-wrap');
    const selection = document.getElementById('car-image-ocr-selection');
    const hint = document.getElementById('car-image-ocr-hint');
    const fullImageButton = document.getElementById('car-image-ocr-full-image');
    const runButton = document.getElementById('car-image-ocr-run');
    const status = document.getElementById('car-image-ocr-status');
    const results = document.getElementById('car-image-ocr-results');
    if (!modal || !carView || !input || !dropzone || !filePicker || !workspace || !preview ||
        !previewWrap || !selection || !hint || !fullImageButton || !runButton || !status || !results) return;

    const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
    const TESSERACT_URL = 'https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js';
    const ALLOWED_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
    let libraryPromise = null;
    let workerPromise = null;
    let worker = null;
    let running = false;
    let imageBitmap = null;
    let selectionRect = null;
    let pointerStart = null;
    let lastOcrCode = '';
    let stateVersion = 0;

    function setStatus(message, state) {
        status.textContent = message;
        status.dataset.state = state || '';
        status.hidden = !message;
    }

    function clearResults() {
        results.replaceChildren();
        results.hidden = true;
    }

    function validImage(file) {
        if (!file) return false;
        const extensionAllowed = /\.(png|jpe?g|webp)$/i.test(file.name || '');
        return (ALLOWED_TYPES.has(file.type) || (!file.type && extensionAllowed)) && file.size <= MAX_IMAGE_BYTES;
    }

    function resetOcrState(clearInput) {
        stateVersion += 1;
        setStatus('', '');
        clearResults();
        workspace.hidden = true;
        filePicker.value = '';
        filePicker.disabled = false;
        runButton.disabled = false;
        fullImageButton.disabled = false;
        selectionRect = null;
        selection.hidden = true;
        preview.width = 0;
        preview.height = 0;
        dropzone.hidden = false;
        hint.textContent = 'Arraste sobre a imagem para marcar a área de recorte.';
        if (imageBitmap && typeof imageBitmap.close === 'function') imageBitmap.close();
        imageBitmap = null;
        if (clearInput && lastOcrCode && input.value === lastOcrCode) input.value = '';
        lastOcrCode = '';
    }

    function normalizeCarsFromOcr(text) {
        const normalized = String(text || '')
            .normalize('NFKC')
            .toUpperCase()
            .replace(/[‐‑‒–—−﹣－]/g, '-')
            .replace(/\s+/g, '');
        return Array.from(new Set(normalized.match(/[A-Z]{2}-[0-9]{7}-[A-F0-9]{32}/g) || []));
    }

    function loadTesseract() {
        if (window.Tesseract) return Promise.resolve(window.Tesseract);
        if (libraryPromise) return libraryPromise;
        libraryPromise = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = TESSERACT_URL;
            script.async = true;
            script.onload = () => window.Tesseract
                ? resolve(window.Tesseract)
                : reject(new Error('A biblioteca OCR não foi inicializada.'));
            script.onerror = () => reject(new Error('Não foi possível carregar a biblioteca OCR.'));
            document.head.appendChild(script);
        }).catch((error) => {
            libraryPromise = null;
            throw error;
        });
        return libraryPromise;
    }

    async function getWorker() {
        if (worker) return worker;
        if (!workerPromise) workerPromise = loadTesseract().then((tesseract) => tesseract.createWorker('eng', 1));
        try {
            worker = await workerPromise;
            return worker;
        } catch (error) {
            workerPromise = null;
            throw error;
        }
    }

    async function displayImage(file) {
        resetOcrState(true);
        const version = stateVersion;
        if (!file || !validImage(file)) {
            setStatus(file && file.size > MAX_IMAGE_BYTES
                ? 'A imagem deve ter no máximo 5 MB.'
                : 'Selecione uma imagem PNG, JPG, JPEG ou WEBP de até 5 MB.', 'error');
            return;
        }

        try {
            const decodedBitmap = await createImageBitmap(file);
            if (version !== stateVersion) {
                decodedBitmap.close();
                return;
            }
            imageBitmap = decodedBitmap;
            const scale = Math.min(1, 1400 / imageBitmap.width, 800 / imageBitmap.height);
            preview.width = Math.max(1, Math.round(imageBitmap.width * scale));
            preview.height = Math.max(1, Math.round(imageBitmap.height * scale));
            preview.getContext('2d').drawImage(imageBitmap, 0, 0, preview.width, preview.height);
            dropzone.hidden = true;
            workspace.hidden = false;
            setStatus('', '');
        } catch (error) {
            if (version !== stateVersion) return;
            resetOcrState(false);
            setStatus('Não foi possível abrir essa imagem. Tente outro arquivo.', 'error');
        }
    }

    function drawSelection(rect) {
        selectionRect = rect;
        if (!rect || rect.width < 4 || rect.height < 4) {
            selection.hidden = true;
            selectionRect = null;
            hint.textContent = 'Usando a imagem inteira. Arraste sobre ela para selecionar um recorte.';
            return;
        }
        selection.hidden = false;
        selection.style.left = `${rect.x}px`;
        selection.style.top = `${rect.y}px`;
        selection.style.width = `${rect.width}px`;
        selection.style.height = `${rect.height}px`;
        hint.textContent = 'O OCR será executado na área marcada. Arraste para ajustar o recorte.';
    }

    function imageForOcr() {
        if (!imageBitmap || !selectionRect) return preview;
        const bounds = preview.getBoundingClientRect();
        const scaleX = preview.width / bounds.width;
        const scaleY = preview.height / bounds.height;
        const sx = Math.max(0, Math.round(selectionRect.x * scaleX));
        const sy = Math.max(0, Math.round(selectionRect.y * scaleY));
        const sw = Math.min(preview.width - sx, Math.round(selectionRect.width * scaleX));
        const sh = Math.min(preview.height - sy, Math.round(selectionRect.height * scaleY));
        if (sw < 2 || sh < 2) return preview;
        const crop = document.createElement('canvas');
        crop.width = sw;
        crop.height = sh;
        crop.getContext('2d').drawImage(preview, sx, sy, sw, sh, 0, 0, sw, sh);
        return crop;
    }

    function showCars(codes) {
        clearResults();
        const intro = document.createElement('p');
        intro.textContent = codes.length === 1 ? 'CAR encontrado. Confira o código antes de consultar.' : `${codes.length} CARs encontrados. Escolha e confira o código antes de consultar.`;
        results.appendChild(intro);
        const list = document.createElement('div');
        list.className = 'car-image-ocr-code-list';
        codes.forEach((code) => {
            const choice = document.createElement('button');
            choice.type = 'button';
            choice.textContent = code;
            choice.addEventListener('click', () => {
                input.value = code;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                lastOcrCode = code;
                setStatus('CAR identificado. Confira o código antes de consultar.', 'success');
            });
            list.appendChild(choice);
        });
        results.appendChild(list);
        results.hidden = false;
        input.value = codes[0];
        lastOcrCode = codes[0];
    }

    async function runOcr() {
        if (running || !imageBitmap) return;
        running = true;
        clearResults();
        setStatus('Lendo imagem...', 'busy');
        runButton.disabled = true;
        fullImageButton.disabled = true;
        filePicker.disabled = true;
        const activeWorkerPromise = getWorker();
        let activeWorker = null;
        const version = stateVersion;
        try {
            activeWorker = await activeWorkerPromise;
            const result = await activeWorker.recognize(imageForOcr());
            if (version !== stateVersion) return;
            const extractedText = String(result?.data?.text || '').trim();
            const codes = normalizeCarsFromOcr(extractedText);
            setStatus('', '');
            if (codes.length) {
                showCars(codes);
            } else if (extractedText) {
                setStatus('Não encontramos um código CAR válido automaticamente.', 'error');
                const text = document.createElement('pre');
                text.className = 'car-image-ocr-extracted-text';
                text.textContent = extractedText;
                results.replaceChildren(text);
                results.hidden = false;
            } else {
                setStatus('Não foi possível identificar texto na imagem.', 'error');
            }
        } catch (error) {
            if (version === stateVersion) setStatus('Não foi possível ler a imagem. Tente novamente ou digite o código.', 'error');
        } finally {
            running = false;
            if (version === stateVersion) {
                runButton.disabled = false;
                fullImageButton.disabled = false;
                filePicker.disabled = false;
            }
            if (modal.hidden) {
                const workerToTerminate = worker || activeWorker;
                worker = null;
                workerPromise = null;
                if (workerToTerminate) workerToTerminate.terminate().catch(() => {});
            }
        }
    }

    filePicker.addEventListener('change', () => {
        const file = filePicker.files && filePicker.files[0];
        if (file) displayImage(file);
    });
    dropzone.addEventListener('click', (event) => {
        if (event.target.closest('label')) return;
        filePicker.click();
    });
    dropzone.addEventListener('keydown', (event) => {
        if (event.target !== dropzone || !['Enter', ' '].includes(event.key)) return;
        event.preventDefault();
        filePicker.click();
    });
    ['dragenter', 'dragover'].forEach((type) => dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        dropzone.classList.add('is-dragging');
    }));
    ['dragleave', 'drop'].forEach((type) => dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        dropzone.classList.remove('is-dragging');
    }));
    dropzone.addEventListener('drop', (event) => {
        const file = Array.from(event.dataTransfer?.files || []).find((item) =>
            ALLOWED_TYPES.has(item.type) || (!item.type && /\.(png|jpe?g|webp)$/i.test(item.name || '')));
        if (file) displayImage(file);
        else setStatus('Solte uma imagem PNG, JPG, JPEG ou WEBP.', 'error');
    });

    document.addEventListener('paste', (event) => {
        if (modal.hidden || carView.hidden || !event.clipboardData) return;
        const image = Array.from(event.clipboardData.items || [])
            .find((item) => item.kind === 'file' && ALLOWED_TYPES.has(item.type));
        if (!image) return;
        event.preventDefault();
        displayImage(image.getAsFile());
    });

    preview.addEventListener('pointerdown', (event) => {
        if (!imageBitmap || workspace.hidden) return;
        const bounds = preview.getBoundingClientRect();
        pointerStart = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
        preview.setPointerCapture(event.pointerId);
        drawSelection({ ...pointerStart, width: 0, height: 0 });
    });
    preview.addEventListener('pointermove', (event) => {
        if (!pointerStart) return;
        const bounds = preview.getBoundingClientRect();
        const current = {
            x: Math.max(0, Math.min(pointerStart.x, event.clientX - bounds.left)),
            y: Math.max(0, Math.min(pointerStart.y, event.clientY - bounds.top)),
            width: Math.min(preview.width, Math.abs(event.clientX - bounds.left - pointerStart.x)),
            height: Math.min(preview.height, Math.abs(event.clientY - bounds.top - pointerStart.y))
        };
        drawSelection(current);
    });
    ['pointerup', 'pointercancel'].forEach((type) => preview.addEventListener(type, () => { pointerStart = null; }));
    fullImageButton.addEventListener('click', () => drawSelection(null));
    runButton.addEventListener('click', runOcr);

    const modalObserver = new MutationObserver(() => {
        if (modal.hidden) {
            resetOcrState(true);
            if (workerPromise) {
                const pendingWorker = worker;
                worker = null;
                workerPromise = null;
                if (pendingWorker) pendingWorker.terminate().catch(() => {});
            }
        } else if (!carView.hidden) {
            resetOcrState(true);
        }
    });
    modalObserver.observe(modal, { attributes: true, attributeFilter: ['hidden'] });
    const carViewObserver = new MutationObserver(() => {
        if (!carView.hidden) resetOcrState(true);
    });
    carViewObserver.observe(carView, { attributes: true, attributeFilter: ['hidden'] });
})();
