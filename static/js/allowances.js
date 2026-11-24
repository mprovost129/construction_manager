document.querySelectorAll('[data-allowance-section]').forEach(section => {
  const editor = section.querySelector('[data-group-editor]');
  if (!editor) return;
  const rows = editor.querySelector('[data-group-rows]');
  const hidden = editor.querySelector('input[type="hidden"]');
  const sectionAmount = section.querySelector('[data-section-amount] input');
  const sync = () => {
    const groups = [...rows.querySelectorAll('[data-budget-group]')].map(row => ({
      id: row.dataset.groupId,
      name: row.querySelector('[data-group-name]').value.trim(),
      amount: row.querySelector('[data-group-amount]').value,
    }));
    hidden.value = JSON.stringify(groups);
    sectionAmount.readOnly = groups.length > 0;
    const total = editor.querySelector('[data-group-total]');
    total.hidden = !groups.length;
    if (groups.length) {
      const cents = groups.reduce((sum, group) => sum + Math.round(Number(group.amount || 0) * 100), 0);
      sectionAmount.value = (cents / 100).toFixed(2);
      total.textContent = `Section allowance: ${(cents / 100).toLocaleString('en-US', {style: 'currency', currency: 'USD'})} — calculated from the groups above.`;
    }
    section.querySelectorAll('[data-row-group]').forEach(wrapper => {
      const select = wrapper.querySelector('select');
      const previous = select.value;
      select.replaceChildren(new Option(groups.length ? 'Choose allowance group' : 'Shared section allowance', ''));
      groups.forEach(group => select.add(new Option(group.name || 'Unnamed group', group.id)));
      if (previous && !groups.some(group => group.id === previous)) {
        select.add(new Option('Removed group — choose another', previous));
      }
      select.value = previous;
      wrapper.hidden = wrapper.closest('[data-allowance-row]').querySelector('select').value !== 'section' || (!groups.length && !previous);
    });
    section.dispatchEvent(new Event('allowance-groups-changed'));
  };
  editor.addEventListener('input', sync);
  editor.addEventListener('click', event => {
    const remove = event.target.closest('[data-remove-group]');
    if (remove) { remove.closest('[data-budget-group]').remove(); sync(); }
  });
  editor.querySelector('[data-add-group]').addEventListener('click', () => {
    if (rows.children.length >= 50) return;
    const row = editor.querySelector('template').content.firstElementChild.cloneNode(true);
    row.dataset.groupId = crypto.randomUUID();
    rows.append(row);
    section.querySelector('input[type="checkbox"]').checked = true;
    section.querySelector('input[type="checkbox"]').dispatchEvent(new Event('change'));
    sync();
    row.querySelector('[data-group-name]').focus();
  });
  section.querySelectorAll('[data-allowance-row] > td > select').forEach(select => select.addEventListener('change', sync));
  sync();
});
document.querySelectorAll('[data-allowance-section]').forEach(section => {
  const toggle = section.querySelector('input[type="checkbox"]');
  const amount = section.querySelector('[data-section-amount]');
  const status = section.querySelector('[data-section-status]');
  const sources = [...section.querySelectorAll('[data-allowance-row]:not([data-inactive]) > td > select')];
  const refresh = () => {
    amount.hidden = !toggle.checked;
    if (!status) return;
    status.hidden = !toggle.checked;
    const shared = sources.filter(select => select.value === 'section').length;
    const missing = sources.filter(select => select.value === 'section' &&
      section.querySelector('[data-budget-group]') &&
      !select.closest('[data-allowance-row]').querySelector('[data-row-group] select').value).length;
    status.textContent = missing
      ? `${missing} selections still need an allowance group. Choose their group below before saving.`
      : shared
      ? `${shared} of ${sources.length} selections draw from this shared allowance. Own allowance and No allowance items stay separate.`
      : sources.length
        ? 'Not applied: no selections draw from this budget yet. Choose Use section allowance below, or use the button above to assign all items.'
        : 'Add selections to this section, then choose Use section allowance for the items this budget covers.';
  };
  toggle.addEventListener('change', refresh);
  section.addEventListener('allowance-groups-changed', refresh);
  section.querySelectorAll('[data-row-group] select').forEach(select => select.addEventListener('change', refresh));
  sources.forEach(select => select.addEventListener('change', refresh));
  section.querySelector('[data-use-section]')?.addEventListener('click', () => {
    toggle.checked = true;
    refresh();
    sources.forEach(select => {
      select.value = 'section';
      select.dispatchEvent(new Event('change'));
    });
  });
  refresh();
});
const source = document.querySelector('[data-allowance-editor] select[name="allowance_mode"]');
if (source) {
  const amount = document.querySelector('[data-allowance-editor] input[name="allowance_amount"]').closest('.form-field');
  const refresh = () => { amount.hidden = source.value !== 'individual'; };
  source.addEventListener('change', refresh);
  refresh();
}
document.querySelectorAll('[data-allowance-row]').forEach(row => {
  const mode = row.querySelector('select');
  const amount = row.querySelector('[data-own-amount]');
  const refresh = () => { amount.hidden = mode.value !== 'individual'; };
  mode.addEventListener('change', refresh);
  refresh();
});
