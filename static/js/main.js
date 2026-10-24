(() => {
    'use strict';

    const navigationToggle = document.querySelector('.app-nav__toggle');
    const navigationMenu = document.querySelector('.app-nav__menu');
    if (navigationToggle && navigationMenu) {
        navigationMenu.setAttribute('data-collapsible', '');
        navigationToggle.hidden = false;
        navigationToggle.addEventListener('click', () => {
            const isOpen = navigationToggle.getAttribute('aria-expanded') === 'true';
            navigationToggle.setAttribute('aria-expanded', String(!isOpen));
            navigationToggle.querySelector('.visually-hidden').textContent = isOpen
                ? 'Open navigation'
                : 'Close navigation';
            navigationMenu.toggleAttribute('data-open', !isOpen);
        });
    }

    const installButton = document.querySelector('[data-install-app]');
    if (installButton) {
        let installPrompt;
        window.addEventListener('beforeinstallprompt', (event) => {
            event.preventDefault();
            installPrompt = event;
            installButton.hidden = false;
        });
        installButton.addEventListener('click', async () => {
            if (!installPrompt) {
                return;
            }
            installPrompt.prompt();
            await installPrompt.userChoice;
            installPrompt = undefined;
            installButton.hidden = true;
        });
    }

    const serviceWorkerUrl = document.body.dataset.serviceWorkerUrl;
    if ('serviceWorker' in navigator && serviceWorkerUrl) {
        window.addEventListener('load', () => {
            navigator.serviceWorker.register(serviceWorkerUrl, {scope: '/'});
        });
    }

    const submitControlSelector = [
        'button[type="submit"]',
        'button:not([type])',
        'input[type="submit"]',
    ].join(', ');

    function resetSubmissionLock(form) {
        delete form.dataset.submitting;
        form.removeAttribute('aria-busy');
        form.querySelectorAll('[data-submission-lock="true"]').forEach((control) => {
            control.disabled = false;
            control.removeAttribute('data-submission-lock');
            control.removeAttribute('aria-busy');
            control.classList.remove('is-submitting');
        });
    }

    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) {
            return;
        }
        if (form.method.toLowerCase() !== 'post') {
            return;
        }
        if (form.dataset.allowMultipleSubmissions === 'true') {
            return;
        }
        if (form.dataset.submitting === 'true') {
            event.preventDefault();
            return;
        }

        form.dataset.submitting = 'true';
        form.setAttribute('aria-busy', 'true');
        form.querySelectorAll(submitControlSelector).forEach((control) => {
            if (control.disabled) {
                return;
            }
            control.disabled = true;
            control.dataset.submissionLock = 'true';
        });
        if (event.submitter) {
            event.submitter.classList.add('is-submitting');
            event.submitter.setAttribute('aria-busy', 'true');
        }
    });

    window.addEventListener('pageshow', () => {
        document.querySelectorAll('form[data-submitting="true"]').forEach(
            resetSubmissionLock,
        );
    });
})();
