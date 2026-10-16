/** Downscale a canvas region to a small grayscale fingerprint. */
export function thumbnail(source, w = 64, h = 36) {
  const c = typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(w, h) : Object.assign(document.createElement('canvas'), { width: w, height: h });
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(source, 0, 0, w, h);
  return grayscale(ctx.getImageData(0, 0, w, h).data);
}

/** RGBA bytes → grayscale Uint8Array. */
export function grayscale(rgba) {
  const out = new Uint8Array(rgba.length / 4);
  for (let i = 0, j = 0; i < rgba.length; i += 4, j++) out[j] = (rgba[i] * 0.299 + rgba[i + 1] * 0.587 + rgba[i + 2] * 0.114) | 0;
  return out;
}

/** True when the mean absolute pixel difference exceeds the threshold (0–255 scale). */
export function changed(a, b, threshold = 2.0) {
  if (!a || !b || a.length !== b.length) return true;
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += Math.abs(a[i] - b[i]);
  return sum / a.length > threshold;
}
