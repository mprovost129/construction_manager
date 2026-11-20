(() => {
    'use strict';

    const sheet = document.querySelector('[data-recovery-sheet]');
    const actions = document.querySelector('[data-recovery-actions]');
    if (!sheet || !actions) return;

    const status = document.querySelector('[data-recovery-status]');
    actions.hidden = false;
    document.querySelector('[data-recovery-download]').addEventListener('click', () => {
        // Use only the one-time page. Never send codes elsewhere or persist them
        // in browser storage; the user chooses where to keep the downloaded file.
        let url;
        let link;
        try {
            const codes = Array.from(sheet.querySelectorAll('code'), (code) => code.textContent.trim());
            const text = [
                `${sheet.dataset.appName} recovery codes`,
                sheet.querySelector('[data-recovery-account]').textContent.trim(),
                `Saved: ${new Date().toISOString()}`,
                '',
                ...codes.map((code, index) => `${index + 1}. ${code}`),
                '',
                'Each code can be used once, together with your password.',
                'Previous recovery codes no longer work.',
                'Keep this file private and separate from your phone.',
                'Anyone with your password and an unused code can sign in to your account.',
                '',
            ].join('\r\n');
            url = URL.createObjectURL(new Blob([text], {type: 'text/plain;charset=utf-8'}));
            link = document.createElement('a');
            link.href = url;
            link.download = 'heritage360-recovery-codes.txt';
            link.hidden = true;
            document.body.appendChild(link);
            link.click();
            status.textContent = 'Download requested. Check that the file is saved before leaving this page.';
        } catch {
            status.textContent = 'The download could not start. Print or copy your codes before leaving this page.';
        } finally {
            if (link) link.remove();
            if (url) window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        }
    });

    document.querySelector('[data-recovery-print]').addEventListener('click', () => {
        window.print();
    });
})();
