// Carruseles sin dependencias. Pausan con foco, puntero, pestaña oculta o movimiento reducido.
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
document.querySelectorAll('[data-carousel]').forEach(root => {
  const slides = Array.from(root.querySelectorAll('[data-slide]'));
  const selectors = Array.from(root.querySelectorAll('[data-go]'));
  const pauseButton = root.querySelector('[data-pause]');
  const count = root.querySelector('[data-carousel-count]');
  const status = root.querySelector('[data-carousel-status]');
  const interval = Number(root.dataset.autoplay) || 5000;
  let index = 0;
  let paused = reducedMotion.matches;
  let hovered = false;
  let focused = false;
  let visible = true;
  let timer = null;

  function show(next, announce = false) {
    index = (next + slides.length) % slides.length;
    slides.forEach((slide, i) => { slide.hidden = i !== index; });
    selectors.forEach(button => {
      const active = Number(button.dataset.go) === index;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    if (count) count.textContent = `${index + 1} / ${slides.length}`;
    if (status && announce) status.textContent = `Foto ${index + 1} de ${slides.length}`;
  }

  function sync() {
    if (timer !== null) window.clearInterval(timer);
    timer = null;
    if (pauseButton) {
      pauseButton.textContent = paused ? '▶' : 'Ⅱ';
      pauseButton.setAttribute('aria-label', paused ? 'Activar cambio automático' : 'Pausar cambio automático');
      pauseButton.setAttribute('aria-pressed', String(paused));
    }
    if (slides.length > 1 && !paused && !hovered && !focused && !document.hidden && visible) {
      timer = window.setInterval(() => show(index + 1), interval);
    }
  }

  function manual(next) {
    show(next, true);
    paused = true; // Una elección manual queda visible hasta que el usuario pulse reproducir.
    sync();
  }

  if (slides.length < 2) return;
  root.querySelector('[data-next]')?.addEventListener('click', () => manual(index + 1));
  root.querySelector('[data-prev]')?.addEventListener('click', () => manual(index - 1));
  selectors.forEach(button => button.addEventListener('click', () => manual(Number(button.dataset.go))));
  pauseButton?.addEventListener('click', () => {
    paused = !paused;
    // Reproducir es una orden explícita, incluso con el botón todavía enfocado.
    if (!paused) { focused = false; hovered = false; }
    sync();
  });
  root.addEventListener('mouseenter', () => { hovered = true; sync(); });
  root.addEventListener('mouseleave', () => { hovered = false; sync(); });
  root.addEventListener('focusin', () => { focused = true; sync(); });
  root.addEventListener('focusout', event => {
    if (!root.contains(event.relatedTarget)) { focused = false; sync(); }
  });
  root.addEventListener('keydown', event => {
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
      event.preventDefault();
      manual(index + (event.key === 'ArrowRight' ? 1 : -1));
    }
  });
  document.addEventListener('visibilitychange', sync);
  reducedMotion.addEventListener('change', event => { if (event.matches) paused = true; sync(); });
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(entries => { visible = entries[0].isIntersecting; sync(); }).observe(root);
  }
  show(0);
  sync();
});

document.querySelectorAll('form[data-confirm]').forEach(form => {
  form.addEventListener('submit', event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

// Elegir una foto de producto por su miniatura.
document.querySelectorAll('[data-cover-picker] input').forEach(input => {
  input.addEventListener('change', () => {
    input.closest('form').querySelector('select[name="foto_producto"]').value = input.value;
  });
});
