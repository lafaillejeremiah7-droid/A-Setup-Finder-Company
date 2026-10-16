import { createWorker } from 'tesseract.js';

/** Self-hosted Tesseract worker — every asset loads from this site, never a CDN. */
export async function createOcr(onStatus = () => {}) {
  const base = new URL(import.meta.env.BASE_URL + 'ocr/', location.href).href;
  const worker = await createWorker('eng', 1 /* LSTM only */, {
    workerPath: base + 'worker.min.js',
    corePath: base,
    langPath: base,
    workerBlobURL: false,
    cacheMethod: 'none',
    logger: (m) => m.status && onStatus(m.status, m.progress),
  });
  return {
    /**
     * @param {HTMLCanvasElement} canvas
     * @returns {Promise<{paragraphs:{lines:{words:{text:string,confidence:number}[]}[]}[]}>}
     */
    async recognize(canvas) {
      const { data } = await worker.recognize(canvas, {}, { blocks: true, text: false });
      const paragraphs = [];
      for (const block of data.blocks || [])
        for (const p of block.paragraphs || [])
          paragraphs.push({ lines: (p.lines || []).map((l) => ({ words: (l.words || []).map((w) => ({ text: w.text, confidence: w.confidence })) })) });
      return { paragraphs };
    },
    terminate: () => worker.terminate(),
  };
}
