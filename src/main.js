import './styles.css';
// Pull harper's WASM into the build graph so the bundler emits it as an asset.
import { binary } from 'harper.js/binary';

const left = document.getElementById('left');
const right = document.getElementById('right');
left.textContent = 'English Coach is loading…';
right.textContent = '';
// Referenced so the import is not tree-shaken; the real app wires the linter in later.
window.__harperBinary = binary;
