/* A fresh managed challenge for every browser mutation, including file uploads.
 * No form contents are sent to Cloudflare. The server validates the token.
 */
(() => {
    'use strict';
    const config = document.getElementById('heritage-form-security');
    const pending = new WeakMap();
    const verified = new WeakSet();
    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        const submitter = event.submitter;
        const method = (submitter?.getAttribute('formmethod') || form.method || 'get').toLowerCase();
        if (method === 'get' || method === 'dialog') return;
        if (verified.has(form)) {
            verified.delete(form);
            return;
        }
        event.preventDefault();
        if (pending.has(form)) return;
        form.querySelectorAll(':scope > .form-security-check').forEach(check => check.remove());
        const container = document.createElement('div');
        container.className = 'form-security-check';
        container.style.cssText = 'display:block;min-width:300px;margin:12px 0';
        const status = document.createElement('p');
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        status.textContent = 'Completing security check…';
        const widget = document.createElement('div');
        container.append(status, widget);
        form.append(container);
        const fail = () => {
            pending.delete(form);
            status.textContent = 'Security verification could not finish. Your form has not been sent. Please submit again to retry.';
        };
        if (!window.turnstile || !config?.dataset.sitekey) { fail(); return; }
        let widgetId;
        const cleanup = () => {
            if (widgetId !== undefined) window.turnstile.remove(widgetId);
            pending.delete(form);
        };
        pending.set(form, true);
        try {
            widgetId = window.turnstile.render(widget, {
                sitekey: config.dataset.sitekey,
                action: 'heritage_form',
                appearance: 'interaction-only',
                'response-field': false,
                callback: (token) => {
                    cleanup();
                    container.remove();
                    let field = form.querySelector('input[name="cf-turnstile-response"]');
                    if (!field) {
                        field = document.createElement('input');
                        field.type = 'hidden';
                        field.name = 'cf-turnstile-response';
                        form.append(field);
                    }
                    field.value = token;
                    verified.add(form);
                    // Preserve HTML validation and the clicked button's name,
                    // value, action, and method (important for Django admin).
                    try { form.requestSubmit(submitter || undefined); }
                    finally { verified.delete(form); }
                },
                'error-callback': () => { cleanup(); fail(); },
                'expired-callback': () => { cleanup(); fail(); },
                'timeout-callback': () => { cleanup(); fail(); },
            });
        } catch { cleanup(); fail(); }
    }, true);
    window.addEventListener('pageshow', () => {
        document.querySelectorAll('input[name="cf-turnstile-response"]').forEach(field => { field.value = ''; });
    });
})();
