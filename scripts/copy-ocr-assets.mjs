// Copies the self-hosted Tesseract assets into public/ocr/ so OCR never hits a CDN.
import { copyFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const nm = join(root, 'node_modules');
const out = join(root, 'public', 'ocr');

const files = [
  ['tesseract.js/dist/worker.min.js', 'worker.min.js'],
  ['tesseract.js-core/tesseract-core-lstm.wasm.js', 'tesseract-core-lstm.wasm.js'],
  ['tesseract.js-core/tesseract-core-simd-lstm.wasm.js', 'tesseract-core-simd-lstm.wasm.js'],
  ['tesseract.js-core/tesseract-core-relaxedsimd-lstm.wasm.js', 'tesseract-core-relaxedsimd-lstm.wasm.js'],
  ['@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz', 'eng.traineddata.gz'],
];

const missing = files.filter(([src]) => !existsSync(join(nm, src))).map(([src]) => src);
if (missing.length) {
  console.error(`copy-ocr-assets: missing ${missing.length} file(s):\n  ${missing.join('\n  ')}\nRun npm install.`);
  process.exit(1);
}

mkdirSync(out, { recursive: true });
for (const [src, dest] of files) copyFileSync(join(nm, src), join(out, dest));
console.log(`copy-ocr-assets: copied ${files.length} files to public/ocr/`);
