(function () {
    'use strict';

    const config = document.getElementById('app-config');
    const notice = document.getElementById('free-feature-notice');
    if (!config || config.dataset.freeMode !== 'true' || !notice) return;

    const title = notice.querySelector('#free-feature-title');
    const message = notice.querySelector('#free-feature-message');
    const defaultTitle = 'Recurso disponível nos planos do CONFRONTA.';

    function showNotice(noticeTitle, noticeMessage) {
        if (title) title.textContent = noticeTitle || defaultTitle;
        if (message) message.textContent = noticeMessage || '';
        notice.hidden = false;
        const close = notice.querySelector('[data-free-notice-close]');
        if (close) close.focus();
    }

    window.CONFRONTA_SHOW_FREE_CAR_PAYWALL = function () {
        showNotice(
            'Conheça os detalhes deste imóvel',
            'Contrate um plano do CONFRONTA para acessar os dados e análises deste CAR.'
        );
    };

    document.addEventListener('click', function (event) {
        const locked = event.target.closest('[data-premium-lock]');
        if (!locked) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        showNotice();
    }, true);

    document.addEventListener('submit', function (event) {
        if (!event.target.matches('[data-premium-lock]')) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        showNotice();
    }, true);

    notice.addEventListener('click', function (event) {
        if (event.target === notice || event.target.closest('[data-free-notice-close]')) {
            notice.hidden = true;
        }
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && !notice.hidden) notice.hidden = true;
    });
}());
