/** 图片查看器：使用页面内弹窗查看、局部缩放与关闭，统一拦截带 data-image-viewer 的图片链接。 */
document.addEventListener('DOMContentLoaded', () => {
  const dialog = document.getElementById('image-viewer');
  if (!dialog) return;
  const image = document.getElementById('image-viewer-image');
  const canvas = document.getElementById('image-viewer-canvas');
  const title = document.getElementById('image-viewer-title');
  const status = document.getElementById('image-viewer-status');
  let trigger = null;
  let zoom = 1;
  let drag = null;
  let suppressClick = false;
  const fitButton = document.getElementById('image-viewer-fit');
  const zoomButton = document.getElementById('image-viewer-zoom');
  function fit() {
    zoom = 1;
    canvas.classList.remove('zoomed', 'is-dragging');
    image.style.removeProperty('width');
    image.style.removeProperty('height');
    canvas.scrollTop = canvas.scrollLeft = 0;
    fitButton.setAttribute('aria-pressed', 'true');
    zoomButton.setAttribute('aria-pressed', 'false');
  }
  function magnify(factor, clientX, clientY) {
    if (!image.naturalWidth || !image.naturalHeight) return;
    if (factor <= 1) { fit(); return; }
    const box = image.getBoundingClientRect();
    const scale = zoom === 1 ? Math.min(box.width / image.naturalWidth, box.height / image.naturalHeight) : box.width / image.naturalWidth;
    const width = image.naturalWidth * scale;
    const height = image.naturalHeight * scale;
    const left = box.left + (box.width - width) / 2;
    const top = box.top + (box.height - height) / 2;
    const anchorX = Math.max(0, Math.min(1, (clientX - left) / width));
    const anchorY = Math.max(0, Math.min(1, (clientY - top) / height));
    const bounds = canvas.getBoundingClientRect();
    const style = getComputedStyle(canvas);
    const paddingX = parseFloat(style.paddingLeft);
    const paddingY = parseFloat(style.paddingTop);
    const base = Math.min((canvas.clientWidth - paddingX * 2) / image.naturalWidth,
      (canvas.clientHeight - paddingY * 2) / image.naturalHeight);
    zoom = Math.max(1, Math.min(8, factor));
    canvas.classList.add('zoomed');
    const newWidth = image.naturalWidth * base * zoom;
    const newHeight = image.naturalHeight * base * zoom;
    image.style.width = `${newWidth}px`;
    image.style.height = `${newHeight}px`;
    canvas.scrollLeft = paddingX + anchorX * newWidth - (clientX - bounds.left - canvas.clientLeft);
    canvas.scrollTop = paddingY + anchorY * newHeight - (clientY - bounds.top - canvas.clientTop);
    fitButton.setAttribute('aria-pressed', 'false');
    zoomButton.setAttribute('aria-pressed', 'true');
  }
  fitButton.addEventListener('click', fit);
  zoomButton.addEventListener('click', () => {
    const box = canvas.getBoundingClientRect();
    magnify(zoom === 1 ? 2.5 : zoom * 1.5, box.left + canvas.clientWidth / 2, box.top + canvas.clientHeight / 2);
  });
  canvas.addEventListener('click', event => {
    if (suppressClick) { suppressClick = false; return; }
    if (event.target === image) magnify(zoom === 1 ? 2.5 : zoom * 1.5, event.clientX, event.clientY);
  });
  canvas.addEventListener('wheel', event => {
    if (zoom === 1) return;
    event.preventDefault();
    magnify(zoom * (event.deltaY < 0 ? 1.15 : 1 / 1.15), event.clientX, event.clientY);
  }, {passive: false});
  image.draggable = false;
  canvas.addEventListener('pointerdown', event => {
    if (zoom === 1 || event.button !== 0 || event.target !== image) return;
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY, left: canvas.scrollLeft, top: canvas.scrollTop, moved: false};
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    if (drag.moved) {
      canvas.classList.add('is-dragging');
      canvas.scrollLeft = drag.left - dx;
      canvas.scrollTop = drag.top - dy;
    }
  });
  function finishDrag(event) {
    if (!drag || drag.id !== event.pointerId) return;
    suppressClick = drag.moved;
    drag = null;
    canvas.classList.remove('is-dragging');
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  }
  canvas.addEventListener('pointerup', finishDrag);
  canvas.addEventListener('pointercancel', finishDrag);
  document.getElementById('image-viewer-close').addEventListener('click', () => dialog.close());
  image.addEventListener('load', () => { status.textContent = ''; });
  image.addEventListener('error', () => { status.textContent = '图片加载失败，请关闭后重试。'; });
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => {
    document.body.classList.remove('image-viewer-open');
    drag = null;
    suppressClick = false;
    image.removeAttribute('src');
    trigger?.focus({preventScroll: true});
  });
  document.addEventListener('click', event => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target || dialog.contains(target)) return;
    const link = target.closest('a');
    const picture = link?.querySelector('img') || target.closest('img');
    if (!picture || (link && !link.hasAttribute('href'))) return;
    event.preventDefault();
    trigger = link || picture;
    title.textContent = picture.alt || '图片预览';
    image.alt = picture.alt || '放大图片';
    status.textContent = '正在加载图片…';
    fit();
    image.src = link?.href || picture.currentSrc || picture.src;
    document.body.classList.add('image-viewer-open');
    dialog.showModal();
  });
});
