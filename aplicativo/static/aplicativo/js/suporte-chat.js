(function () {
    'use strict';

    const root = document.getElementById('client-comms');
    if (!root) return;

    const chatButton = document.getElementById('client-chat-fab');
    const chatPanel = document.getElementById('client-chat-panel');
    const chatClose = chatPanel && chatPanel.querySelector('[data-close-chat]');
    const chatMessages = document.getElementById('client-chat-messages');
    const chatForm = document.getElementById('client-chat-form');
    const chatText = document.getElementById('client-chat-text');
    const chatBadge = document.getElementById('client-chat-badge');
    const chatStatus = document.getElementById('client-chat-status');

    const noticeButton = document.getElementById('rail-comunicados') || document.getElementById('client-notice-fab');
    const noticePanel = document.getElementById('client-notice-panel');
    const noticeClose = noticePanel && noticePanel.querySelector('[data-close-notices]');
    const noticeBadge = document.getElementById('rail-comunicados-badge') || document.getElementById('client-notice-badge');

    let pollTimer = null;
    let lastSignature = '';

    function csrfToken() {
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        return input ? input.value : '';
    }

    async function post(url, data) {
        const response = await fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                'X-CSRFToken': csrfToken()
            },
            body: new URLSearchParams(data || {})
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.erro || 'Não foi possível concluir a operação.');
        return payload;
    }

    function setBadge(element, value) {
        if (!element) return;
        const count = Number(value || 0);
        element.textContent = String(count);
        element.hidden = count <= 0;
    }

    function renderMessages(items) {
        if (!chatMessages) return;
        const safeItems = Array.isArray(items) ? items : [];
        const signature = safeItems.map((item) => `${item.id}:${item.criado_em}`).join('|');
        if (signature === lastSignature) return;
        lastSignature = signature;

        chatMessages.replaceChildren();
        if (!safeItems.length) {
            const empty = document.createElement('div');
            empty.className = 'client-chat-empty';
            empty.textContent = 'Como podemos ajudar?';
            chatMessages.appendChild(empty);
            return;
        }

        safeItems.forEach((item) => {
            const bubble = document.createElement('article');
            bubble.className = `client-chat-message ${item.autor_eu ? 'is-client' : 'is-support'}`;
            const text = document.createElement('p');
            text.textContent = item.texto;
            const meta = document.createElement('small');
            meta.textContent = `${item.autor_eu ? 'Você' : item.autor_nome} · ${item.criado_em}`;
            bubble.append(text, meta);
            chatMessages.appendChild(bubble);
        });
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    async function markChatRead() {
        if (!chatPanel || chatPanel.hidden) return;
        try {
            await post(chatPanel.dataset.readUrl, {});
            setBadge(chatBadge, 0);
        } catch (error) {
            console.warn(error);
        }
    }

    async function loadChat() {
        if (!chatPanel) return;
        try {
            const response = await fetch(chatPanel.dataset.stateUrl, {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' }
            });
            if (response.ok) {
                const data = await response.json();
                renderMessages(data.mensagens);
                setBadge(chatBadge, data.nao_lidas);
                if (chatStatus && data.status_label) {
                    chatStatus.textContent = data.status === 'ENCERRADO'
                        ? 'Atendimento encerrado. Envie uma mensagem para reabrir.'
                        : `Atendimento ${String(data.status_label).toLowerCase()}.`;
                }
                if (!chatPanel.hidden && Number(data.nao_lidas || 0) > 0) await markChatRead();
            }
        } catch (error) {
            console.warn(error);
        } finally {
            clearTimeout(pollTimer);
            pollTimer = setTimeout(loadChat, chatPanel && !chatPanel.hidden ? 5000 : 15000);
        }
    }

    function toggleChat(open) {
        if (!chatPanel || !chatButton) return;
        chatPanel.hidden = !open;
        chatButton.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) {
            if (noticePanel) noticePanel.hidden = true;
            if (noticeButton) noticeButton.setAttribute('aria-expanded', 'false');
            loadChat();
            markChatRead();
            setTimeout(() => chatText && chatText.focus(), 60);
        }
    }

    if (chatButton) chatButton.addEventListener('click', () => toggleChat(chatPanel.hidden));
    if (chatClose) chatClose.addEventListener('click', () => toggleChat(false));

    if (chatForm) {
        chatForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const texto = String(chatText && chatText.value || '').trim();
            if (!texto) return;
            const submit = chatForm.querySelector('button[type="submit"]');
            if (submit) submit.disabled = true;
            try {
                await post(chatPanel.dataset.sendUrl, { texto });
                chatText.value = '';
                lastSignature = '';
                await loadChat();
            } catch (error) {
                window.alert(error.message);
            } finally {
                if (submit) submit.disabled = false;
            }
        });
    }

    async function markNoticeItem(item) {
        if (!item || !item.classList.contains('is-unread')) return;
        try {
            const data = await post(item.dataset.readUrl, {});
            item.classList.remove('is-unread');
            setBadge(noticeBadge, data.nao_lidos);
        } catch (error) {
            console.warn(error);
        }
    }

    function toggleNotices(open) {
        if (!noticePanel || !noticeButton) return;
        noticePanel.hidden = !open;
        noticeButton.setAttribute('aria-expanded', open ? 'true' : 'false');
        noticeButton.classList.toggle('is-active', open);
        if (open) {
            toggleChat(false);
            noticePanel.querySelectorAll('.client-notice-item.is-unread').forEach(markNoticeItem);
        }
    }

    if (noticeButton) noticeButton.addEventListener('click', () => toggleNotices(noticePanel.hidden));
    if (noticeClose) noticeClose.addEventListener('click', () => toggleNotices(false));

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        toggleChat(false);
        toggleNotices(false);
    });

    loadChat();
})();
