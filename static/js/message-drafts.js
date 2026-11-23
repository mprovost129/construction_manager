(() => {
  'use strict';
  const prefix = 'heritage-message-v1:';
  const user = document.body.dataset.userId;
  const storage = (() => { try { return window.sessionStorage; } catch { return null; } })();
  const remove = key => { try { storage?.removeItem(key); } catch { /* Storage may be disabled. */ } };
  let sent = [];
  try { sent = JSON.parse(document.getElementById('sent-message-tokens')?.textContent || '[]'); } catch { /* Empty receipts. */ }
  if (storage) {
    for (const key of Object.keys(storage).filter(key => key.startsWith(prefix))) {
      try {
        const draft = JSON.parse(storage.getItem(key));
        if (user === 'anonymous' || !key.startsWith(`${prefix}${user}:`) ||
            Date.now() - draft.savedAt > 86400000 || sent.includes(draft.submissionId)) remove(key);
      } catch { remove(key); }
    }
  }
  // Purge tab-local private drafts on logout, even if the following page is cached.
  document.addEventListener('submit', event => {
    if (new URL(event.target.action, location.href).pathname.includes('/logout')) {
      if (storage) Object.keys(storage).filter(key => key.startsWith(prefix)).forEach(remove);
    }
  }, true);
  window.HeritageDrafts = {
    key: () => {
      const params = new URLSearchParams(location.search);
      return `${prefix}${user}:${location.pathname}:org=${params.get('organization') || ''}:project=${params.get('project') || ''}`;
    },
    read(key) { try { return JSON.parse(storage?.getItem(key) || 'null'); } catch { return null; } },
    write(key, value) { try { if (!storage) return false; storage.setItem(key, JSON.stringify({...value, savedAt: Date.now()})); return true; } catch { return false; } },
    remove,
  };
})();
