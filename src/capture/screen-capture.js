import { thumbnail, changed } from './frame-diff.js';

/**
 * Shares a screen/window and emits cropped frames (as canvases) whenever the image changes.
 * One frame is processed at a time; frames that arrive while OCR is busy are skipped.
 */
export class ScreenCapture {
  /**
   * @param {HTMLVideoElement} video  preview element
   * @param {{onFrame:(canvas:HTMLCanvasElement)=>Promise<void>, onEnd?:()=>void, intervalMs?:number}} opts
   */
  constructor(video, { onFrame, onEnd, intervalMs = 2500 }) {
    Object.assign(this, { video, onFrame, onEnd, intervalMs });
    this.stream = null;
    this.crop = null; // normalized {x,y,w,h}
    this.prevThumb = null;
    this.busy = false;
    this.paused = false;
    this.canvas = document.createElement('canvas');
  }

  get active() {
    return !!this.stream;
  }

  async start() {
    this.stream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 5 }, audio: false });
    this.video.srcObject = this.stream;
    await this.video.play().catch(() => {});
    this.stream.getVideoTracks()[0].addEventListener('ended', () => this.stop());
    this.timer = setInterval(() => this.tick(), this.intervalMs);
    setTimeout(() => this.tick(), 600);
  }

  stop() {
    clearInterval(this.timer);
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.video.srcObject = null;
    this.prevThumb = null;
    this.onEnd?.();
  }

  setCrop(crop) {
    this.crop = crop;
    this.prevThumb = null; // force a fresh read
    this.tick();
  }

  /** Force the next tick to OCR even if unchanged. */
  invalidate() {
    this.prevThumb = null;
  }

  async tick() {
    if (!this.stream || this.busy || this.paused) return;
    const v = this.video;
    if (!v.videoWidth) return;
    const c = this.crop || { x: 0, y: 0, w: 1, h: 1 };
    const sx = Math.round(c.x * v.videoWidth);
    const sy = Math.round(c.y * v.videoHeight);
    const sw = Math.max(1, Math.round(c.w * v.videoWidth));
    const sh = Math.max(1, Math.round(c.h * v.videoHeight));
    // upscale small regions — Tesseract reads ~30px glyphs best
    const scale = sw < 1200 ? Math.min(2, 1600 / sw) : 1;
    this.canvas.width = Math.round(sw * scale);
    this.canvas.height = Math.round(sh * scale);
    const ctx = this.canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(v, sx, sy, sw, sh, 0, 0, this.canvas.width, this.canvas.height);
    const thumb = thumbnail(this.canvas);
    const isChange = changed(this.prevThumb, thumb);
    // After every change, do one extra confirmation read so the coach can tell a real
    // correction from an OCR flicker (fixes must be absent on 2 consecutive reads).
    if (!isChange && !this.confirmNext) return;
    this.confirmNext = isChange;
    this.prevThumb = thumb;
    this.busy = true;
    try {
      await this.onFrame(this.canvas);
    } finally {
      this.busy = false;
    }
  }
}
