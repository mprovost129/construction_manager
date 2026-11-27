(() => {
  document.querySelectorAll('[data-report-section]').forEach(link => {
    link.addEventListener('click', () => {
      const details = document.getElementById(link.hash.slice(1));
      if (details) { details.open = true; details.querySelector('summary').focus(); }
    });
  });
  let openedForPrint = [];
  window.addEventListener('beforeprint', () => {
    openedForPrint = [...document.querySelectorAll('.report-selection-detail:not([open])')];
    openedForPrint.forEach(details => { details.open = true; });
  });
  window.addEventListener('afterprint', () => {
    openedForPrint.forEach(details => { details.open = false; }); openedForPrint = [];
  });
})();
