(function () {
    'use strict';

    const header = document.querySelector('[data-home-header]');
    const menu = document.querySelector('[data-home-menu]');
    const menuButton = document.querySelector('[data-home-menu-button]');

    const syncHeader = () => {
        if (!header) return;
        header.classList.toggle('is-scrolled', window.scrollY > 18);
    };

    if (menu && menuButton) {
        menuButton.addEventListener('click', function () {
            const isOpen = menu.classList.toggle('is-open');
            menuButton.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });

        menu.querySelectorAll('a').forEach(function (link) {
            link.addEventListener('click', function () {
                menu.classList.remove('is-open');
                menuButton.setAttribute('aria-expanded', 'false');
            });
        });
    }

    syncHeader();
    window.addEventListener('scroll', syncHeader, { passive: true });
})();
