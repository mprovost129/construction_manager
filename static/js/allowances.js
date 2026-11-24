document.querySelectorAll('[data-allowance-section]').forEach(section => {
  const toggle = section.querySelector('input[type="checkbox"]');
  const amount = section.querySelector('[data-section-amount]');
  const status = section.querySelector('[data-section-status]');
  const sources = [...section.querySelectorAll('[data-allowance-row]:not([data-inactive]) select')];
  const refresh = () => {
    amount.hidden = !toggle.checked;
    if (!status) return;
    status.hidden = !toggle.checked;
    const shared = sources.filter(select => select.value === 'section').length;
    status.textContent = shared
      ? `${shared} of ${sources.length} selections draw from this shared allowance. Own allowance and No allowance items stay separate.`
      : sources.length
        ? 'Not applied: no selections draw from this budget yet. Choose Use section allowance below, or use the button above to assign all items.'
        : 'Add selections to this section, then choose Use section allowance for the items this budget covers.';
  };
  toggle.addEventListener('change', refresh);
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
