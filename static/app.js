document.querySelectorAll('[data-gallery-src]').forEach(button => {
  button.addEventListener('click', () => {
    const main = document.getElementById('main-photo');
    if (!main) return;
    main.src = button.dataset.gallerySrc;
    main.alt = button.dataset.galleryAlt;
    document.querySelectorAll('[data-gallery-src]').forEach(item => {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-pressed', String(active));
    });
  });
});
document.querySelectorAll('form[data-confirm]').forEach(form => {
  form.addEventListener('submit', event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});
