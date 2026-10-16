import { bandFor } from './level.js';
import { wordOverlap } from '../text/tokenize.js';

/** EMA smoothing with band hysteresis so the live meter doesn't jitter. */
export class LevelSmoother {
  constructor({ alpha = 0.35, lowAlpha = 0.6, hysteresis = 0.3, resetOverlap = 0.3 } = {}) {
    Object.assign(this, { alpha, lowAlpha, hysteresis, resetOverlap });
    this.reset();
  }

  reset() {
    this.ema = null;
    this.band = null;
    this.prevText = '';
  }

  /**
   * @param {{score:number, confidence:string}} level
   * @param {string} text
   * @returns {{score:number, band:string}}
   */
  push(level, text) {
    if (this.ema !== null && this.prevText && wordOverlap(this.prevText, text) < this.resetOverlap) this.reset();
    this.prevText = text;
    if (this.ema === null) {
      this.ema = level.score;
      this.band = bandFor(level.score);
    } else {
      const a = level.confidence === 'low' ? this.lowAlpha : this.alpha;
      this.ema += a * (level.score - this.ema);
      const candidate = bandFor(this.ema);
      if (candidate !== this.band) {
        // only switch once we're clearly past the boundary
        const up = bandFor(this.ema - this.hysteresis);
        const down = bandFor(this.ema + this.hysteresis);
        if (up === candidate && down === candidate) this.band = candidate;
      }
    }
    return { score: Math.round(this.ema * 10) / 10, band: this.band };
  }
}
