(() => {
  'use strict';
  let dialog, image, caption, previous, next, opener, items = [], current = 0, touchX;
  const show = index => {
    current = (index + items.length) % items.length;
    const item = items[current]; image.src = item.href;
    image.alt = item.querySelector('img')?.alt || 'Product photo';
    caption.textContent = `${current + 1} of ${items.length} — ${image.alt}`;
    previous.disabled = next.disabled = items.length < 2;
  };
  const setup = () => {
    dialog = document.createElement('dialog'); dialog.className = 'photo-viewer'; dialog.setAttribute('aria-label', 'Product photos');
    const controls = document.createElement('div'); controls.className = 'photo-viewer-controls';
    const button = (text, action) => { const el = document.createElement('button'); el.type = 'button'; el.textContent = text; el.className = 'btn btn-outline-secondary'; el.addEventListener('click', action); controls.append(el); return el; };
    previous = button('Previous', () => show(current - 1)); next = button('Next', () => show(current + 1));
    button('Close', () => dialog.close());
    image = document.createElement('img'); caption = document.createElement('p'); caption.setAttribute('aria-live', 'polite');
    dialog.append(controls, image, caption); document.body.append(dialog);
    dialog.addEventListener('close', () => { image.removeAttribute('src'); opener?.focus(); });
    dialog.addEventListener('keydown', event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); show(current + (event.key === 'ArrowRight' ? 1 : -1)); } });
    image.addEventListener('touchstart', event => { touchX = event.changedTouches[0].clientX; }, {passive: true});
    image.addEventListener('touchend', event => { const delta = event.changedTouches[0].clientX - touchX; if (Math.abs(delta) > 50) show(current + (delta < 0 ? 1 : -1)); }, {passive: true});
  };
  document.addEventListener('click', event => {
    const link = event.target.closest('a[data-photo-viewer]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button) return;
    if (typeof HTMLDialogElement === 'undefined') return;
    event.preventDefault(); if (!dialog) setup(); opener = link;
    const group = link.closest('.selection-choice, .selection-option') || link.closest('[data-photo-group], .choice-gallery') || link.parentElement;
    items = [...group.querySelectorAll('a[data-photo-viewer]')]; show(Math.max(0, items.indexOf(link))); dialog.showModal();
  });
})();
