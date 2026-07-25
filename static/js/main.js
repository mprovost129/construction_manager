(() => {
    'use strict';

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
