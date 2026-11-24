document.querySelectorAll('[data-allowance-section]').forEach(section => {
  const toggle = section.querySelector('input[type="checkbox"]');
  const amount = section.querySelector('[data-section-amount]');
  const refresh = () => { amount.hidden = !toggle.checked; };
  toggle.addEventListener('change', refresh);
  section.querySelector('[data-use-section]')?.addEventListener('click', () => {
    toggle.checked = true;
    refresh();
    section.querySelectorAll('[data-allowance-row] select').forEach(select => {
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
