(() => {
  'use strict';
  const input = document.querySelector('[data-update-photos] input[type="file"]');
  const preview = document.querySelector('[data-photo-previews]');
  if (!input || !preview) return;
  let urls = [];
  const release = () => { urls.forEach(URL.revokeObjectURL); urls = []; };
  input.addEventListener('change', () => {
    release();
    preview.replaceChildren();
    const files = Array.from(input.files || []);
    if (files.length > 20 || files.reduce((n, f) => n + f.size, 0) > 50 * 1024 * 1024) {
      preview.textContent = 'Select up to 20 photos and keep the batch under 50 MB.';
      return;
    }
    files.forEach(file => {
      const figure = document.createElement('figure');
      figure.className = 'daily-log-photo';
      if (/^image\/(jpeg|png|webp)$/.test(file.type)) {
        const img = document.createElement('img');
        img.alt = 'Preview of ' + file.name;
        img.src = URL.createObjectURL(file);
        urls.push(img.src);
        figure.append(img);
      }
      const caption = document.createElement('figcaption');
      caption.textContent = file.name;
      figure.append(caption);
      preview.append(figure);
    });
  });
  window.addEventListener('pagehide', release);
})();
