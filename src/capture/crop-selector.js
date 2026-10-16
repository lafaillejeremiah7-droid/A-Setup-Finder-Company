/**
 * Drag-to-select overlay on top of the preview video. Reports a normalized rect
 * relative to the video's displayed image (accounting for letterboxing).
 */
export function attachCropSelector(overlay, video, onChange) {
  const ctx = overlay.getContext('2d');
  let crop = null;
  let drag = null;

  function imageRect() {
    const r = overlay.getBoundingClientRect();
    const vw = video.videoWidth || 16;
    const vh = video.videoHeight || 9;
    const s = Math.min(r.width / vw, r.height / vh);
    const w = vw * s;
    const h = vh * s;
    return { left: (r.width - w) / 2, top: (r.height - h) / 2, width: w, height: h, r };
  }

  function draw(rect) {
    const { r } = imageRect();
    overlay.width = r.width * devicePixelRatio;
    overlay.height = r.height * devicePixelRatio;
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.clearRect(0, 0, r.width, r.height);
    if (!rect) return;
    ctx.fillStyle = 'rgba(0,0,0,0.45)';
    ctx.fillRect(0, 0, r.width, r.height);
    ctx.clearRect(rect.x, rect.y, rect.w, rect.h);
    ctx.strokeStyle = '#7cc4ff';
    ctx.lineWidth = 2;
    ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
  }

  function toNorm(rect) {
    const ir = imageRect();
    const clamp = (v) => Math.max(0, Math.min(1, v));
    const x0 = clamp((rect.x - ir.left) / ir.width);
    const y0 = clamp((rect.y - ir.top) / ir.height);
    const x1 = clamp((rect.x + rect.w - ir.left) / ir.width);
    const y1 = clamp((rect.y + rect.h - ir.top) / ir.height);
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }

  function toPx(n) {
    const ir = imageRect();
    return { x: ir.left + n.x * ir.width, y: ir.top + n.y * ir.height, w: n.w * ir.width, h: n.h * ir.height };
  }

  const pos = (e) => {
    const r = overlay.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  overlay.addEventListener('pointerdown', (e) => {
    if (!video.srcObject) return;
    overlay.setPointerCapture(e.pointerId);
    drag = pos(e);
  });
  overlay.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const p = pos(e);
    draw({ x: Math.min(drag.x, p.x), y: Math.min(drag.y, p.y), w: Math.abs(p.x - drag.x), h: Math.abs(p.y - drag.y) });
  });
  overlay.addEventListener('pointerup', (e) => {
    if (!drag) return;
    const p = pos(e);
    const rect = { x: Math.min(drag.x, p.x), y: Math.min(drag.y, p.y), w: Math.abs(p.x - drag.x), h: Math.abs(p.y - drag.y) };
    drag = null;
    if (rect.w < 12 || rect.h < 12) return draw(crop && toPx(crop));
    crop = toNorm(rect);
    draw(toPx(crop));
    onChange(crop);
  });
  new ResizeObserver(() => draw(crop && toPx(crop))).observe(overlay);

  return {
    clear() {
      crop = null;
      draw(null);
      onChange(null);
    },
  };
}
