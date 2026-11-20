(() => {
  const actionMenu = document.querySelector('.selection-action-menu');
  document.addEventListener('click', event => {
    if (actionMenu && !actionMenu.contains(event.target)) actionMenu.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && actionMenu?.open) {
      actionMenu.open = false;
      actionMenu.querySelector('summary').focus();
    }
  });
  const editor = document.getElementById('template-rows');
  let unsaved = false;
  const markUnsaved = () => {
    unsaved = true;
    const status = document.querySelector('[data-template-save-status]');
    if (status) status.textContent = 'Unsaved row changes';
  };
  editor?.addEventListener('input', markUnsaved);
  editor?.addEventListener('change', markUnsaved);
  if (editor) {
    window.addEventListener('beforeunload', event => {
      if (!unsaved) return;
      event.preventDefault();
      event.returnValue = '';
    });
    // The security check may defer the first submit. Clear the warning only
    // after it has finished and the browser is actually submitting this form.
    editor.addEventListener('submit', event => {
      queueMicrotask(() => { if (!event.defaultPrevented) unsaved = false; });
    });
  }
  document.querySelectorAll('[data-selection-toggle]').forEach(button => button.addEventListener('click', () => {
    const row = document.getElementById(button.dataset.selectionToggle);
    row.hidden = !row.hidden;
    button.setAttribute('aria-expanded', String(!row.hidden));
    button.textContent = row.hidden ? '+' : '−';
  }));
  document.querySelectorAll('[data-expand-selections]').forEach(button => button.addEventListener('click', () => {
    const expand = button.dataset.expandSelections !== 'open';
    document.querySelectorAll('.selection-choice-row').forEach(row => {row.hidden = !expand;});
    document.querySelectorAll('[data-selection-toggle]').forEach(toggle => {toggle.setAttribute('aria-expanded', String(expand)); toggle.textContent = expand ? '−' : '+';});
    button.dataset.expandSelections = expand ? 'open' : '';
    button.textContent = expand ? 'Collapse choices' : 'Expand choices';
  }));
  document.querySelectorAll('[data-print-selections]').forEach(button => button.addEventListener('click', () => window.print()));
  document.querySelector('[data-add-template-row]')?.addEventListener('click', () => {
    const total = document.getElementById('id_rows-TOTAL_FORMS');
    if (Number(total.value) >= Number(document.getElementById('id_rows-MAX_NUM_FORMS').value)) return;
    const source = document.getElementById('new-template-row');
    const row = source.content.cloneNode(true);
    row.querySelectorAll('[id],[name],[for]').forEach(element => ['id','name','for'].forEach(attr => {
      if (element.hasAttribute(attr)) element.setAttribute(attr, element.getAttribute(attr).replaceAll('__prefix__', total.value));
    }));
    document.getElementById('template-row-body').appendChild(row);
    total.value = String(Number(total.value) + 1);
    markUnsaved();
    document.getElementById('template-row-body').lastElementChild.querySelector('input:not([type="hidden"])')?.focus();
  });
})();
