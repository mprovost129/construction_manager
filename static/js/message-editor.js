(() => {
  'use strict';
  if (document.body.dataset.clientPreview === 'true') return;
  document.querySelectorAll('[data-message-composer]').forEach(form => {
    const body = form.elements.body;
    const html = form.elements.body_html;
    if (!body || !html || !window.Quill) return; // Plain-text form remains usable.
    const host = document.createElement('div');
    body.before(host);
    const quill = new Quill(host, {
      theme: 'snow', formats: ['bold', 'italic', 'list', 'link'],
      modules: {toolbar: [['bold', 'italic'], [{list: 'bullet'}, {list: 'ordered'}], ['link', 'clean']]},
    });
    quill.root.setAttribute('role', 'textbox');
    quill.root.setAttribute('aria-multiline', 'true');
    quill.root.setAttribute('aria-label', body.labels?.[0]?.textContent || 'Message');
    const tools = {bold: 'Bold', italic: 'Italic', link: 'Insert link', clean: 'Remove formatting'};
    host.previousElementSibling?.querySelectorAll('button').forEach(button => {
      const name = button.classList.contains('ql-list') ? (button.value === 'ordered' ? 'Numbered list' : 'Bulleted list') : Object.entries(tools).find(([key]) => button.classList.contains(`ql-${key}`))?.[1];
      if (name) { button.setAttribute('aria-label', name); button.title = name; }
    });
    body.hidden = true;
    const setContent = (text, formatted) => {
      if (formatted) quill.setContents(quill.clipboard.convert({html: formatted}), 'silent');
      else quill.setText(text || '', 'silent');
    };
    setContent(body.value, html.value);
    const status = document.createElement('p');
    status.className = 'message-save-status'; status.setAttribute('role', 'status');
    const audience = document.createElement('div'); audience.className = 'message-audience';
    const staff = form.dataset.audience === 'staff';
    const updateAudience = () => {
      const selected = [...form.querySelectorAll('input[name="recipients"]:checked')]
        .map(input => input.labels?.[0]?.textContent.trim()).filter(Boolean);
      audience.textContent = staff ? `Staff only — ${selected.length ? `you and ${selected.join(', ')}` : 'participants in this staff conversation'}. Clients cannot see these messages.` : 'Client-visible — shared with the client and assigned project team. Keep company-only information in Staff messages.';
    };
    form.prepend(audience); updateAudience();
    const actions = form.querySelector('.form-actions'); actions.before(status);
    const drafts = window.HeritageDrafts; const key = drafts?.key();
    let dirty = false, sending = false;
    const sync = () => { body.value = quill.getText().trimEnd(); html.value = quill.getSemanticHTML(); };
    const save = () => {
      sync(); dirty = Boolean(body.value.trim() || form.elements.subject?.value);
      const ok = drafts?.write(key, {body: body.value, html: html.value,
        subject: form.elements.subject?.value || '', submissionId: form.elements.submission_id.value,
        recipients: [...form.querySelectorAll('input[name="recipients"]:checked')].map(input => input.value)});
      status.textContent = dirty ? (ok ? 'Draft saved in this tab. Cleared when sent, signed out, or after 24 hours.' : 'Draft storage is unavailable. Keep this page open until your message is sent.') : '';
    };
    const draft = drafts?.read(key);
    // Never overwrite a server-returned validation error with an older draft.
    if (draft && !form.querySelector('.errorlist') && !body.value.trim() && !form.elements.subject?.value) {
      setContent(draft.body, draft.html);
      if (form.elements.subject) form.elements.subject.value = draft.subject || '';
      if (/^[0-9a-f-]{36}$/i.test(draft.submissionId || '')) form.elements.submission_id.value = draft.submissionId;
      form.querySelectorAll('input[name="recipients"]').forEach(input => { input.checked = draft.recipients?.includes(input.value) || false; });
      sync(); dirty = Boolean(body.value.trim() || draft.subject);
      status.textContent = dirty ? 'Your unfinished message was recovered. Review the recipients before sending.' : '';
      updateAudience();
    }
    const discard = document.createElement('button'); discard.type = 'button'; discard.className = 'btn btn-outline-secondary'; discard.textContent = 'Discard draft';
    discard.addEventListener('click', () => {
      if (dirty && !window.confirm('Discard this unfinished message?')) return;
      quill.setText('', 'silent'); if (form.elements.subject) form.elements.subject.value = '';
      sync(); drafts?.remove(key); dirty = false; status.textContent = 'Draft discarded.';
    }); actions.append(discard);
    quill.on('text-change', save);
    form.addEventListener('input', event => { if (!host.contains(event.target)) save(); });
    form.addEventListener('change', () => { updateAudience(); save(); });
    form.addEventListener('submit', event => {
      sync();
      if (!body.value.trim() || body.value.length > 10000) {
        event.preventDefault(); status.textContent = !body.value.trim() ? 'Enter a message before sending.' : 'Please shorten your message to 10,000 characters.'; quill.focus(); return;
      }
      if (sending) { event.preventDefault(); return; }
      save();
      queueMicrotask(() => {
        if (event.defaultPrevented) return; // Cloudflare may still be checking.
        sending = true; status.textContent = 'Sending…';
        const button = event.submitter; if (button) button.disabled = true;
        window.setTimeout(() => {
          sending = false; if (button) button.disabled = false;
          status.textContent = 'Delivery has not been confirmed yet. Your draft is saved. You can retry; the same submission will not be posted twice.';
        }, 15000);
      });
    });
    window.addEventListener('beforeunload', event => {
      if (dirty && !sending) { event.preventDefault(); event.returnValue = ''; }
    });
    window.addEventListener('pageshow', () => {
      sending = false; form.querySelectorAll('button[type="submit"], .form-actions button:not([type])').forEach(button => { button.disabled = false; });
    });
  });
})();
