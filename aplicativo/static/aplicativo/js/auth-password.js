(function(){
    'use strict';
    document.querySelectorAll('[data-password-toggle]').forEach(function(button){
        const input = document.getElementById(button.getAttribute('data-password-toggle'));
        if (!input) return;
        button.addEventListener('click', function(){
            const showing = input.type === 'text';
            input.type = showing ? 'password' : 'text';
            button.setAttribute('aria-pressed', showing ? 'false' : 'true');
            button.setAttribute('aria-label', showing ? 'Mostrar senha' : 'Ocultar senha');
            button.classList.toggle('is-showing', !showing);
        });
    });
})();
