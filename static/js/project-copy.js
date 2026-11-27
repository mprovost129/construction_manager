(() => {
  'use strict';
  const all = document.querySelector('[data-select-all-rows]');
  if (!all) return;
  const rows = Array.from(document.querySelectorAll('input[name="selections"]'));
  const update = () => {
    const count = rows.filter(row => row.checked).length;
    all.checked = count === rows.length && count > 0;
    all.indeterminate = count > 0 && count < rows.length;
    all.disabled = rows.length === 0;
  };
  all.addEventListener('change', () => { rows.forEach(row => { row.checked = all.checked; }); update(); });
  rows.forEach(row => row.addEventListener('change', update));
  update();
})();
