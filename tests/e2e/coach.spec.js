import { test, expect } from '@playwright/test';
import { ACADEMIC, K } from '../fixtures/texts.js';

const SHOTS = process.env.SHOT_DIR || 'test-results/shots';

/** Replace getDisplayMedia with a canvas stream showing fake app chrome + writing. */
async function fakeScreen(page) {
  await page.addInitScript(() => {
    let text = 'He go to school every day. The results is very good. I think this thing proves everything.';
    window.__setFakeScreenText = (t) => (text = t);
    navigator.mediaDevices.getDisplayMedia = async () => {
      const c = document.createElement('canvas');
      c.width = 1280;
      c.height = 720;
      const ctx = c.getContext('2d');
      const draw = () => {
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, 1280, 720);
        ctx.fillStyle = '#ddd';
        ctx.fillRect(0, 0, 1280, 40);
        ctx.fillStyle = '#333';
        ctx.font = '16px sans-serif';
        ctx.fillText('File   Edit   View   Insert   Format', 20, 26);
        ctx.fillStyle = '#000';
        ctx.font = '28px serif';
        const words = text.split(' ');
        let line = '';
        let y = 160;
        for (const w of words) {
          if (ctx.measureText(line + w).width > 1000) {
            ctx.fillText(line, 120, y);
            line = '';
            y += 44;
          }
          line += w + ' ';
        }
        ctx.fillText(line, 120, y);
        requestAnimationFrame(draw);
      };
      draw();
      return c.captureStream(5);
    };
  });
}

test('screen share → OCR → issues → meter, then self-correction', async ({ page }) => {
  await fakeScreen(page);
  await page.goto('./');
  await page.getByTestId('share-btn').click();
  // drag a crop box around the writing region
  const box = await page.getByTestId('crop-overlay').boundingBox();
  await page.mouse.move(box.x + box.width * 0.05, box.y + box.height * 0.12);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.95, box.y + box.height * 0.5, { steps: 5 });
  await page.mouse.up();

  await expect(page.getByTestId('ocr-status')).toContainText('Read', { timeout: 60000 });
  await expect(page.getByTestId('issue-card').filter({ hasText: 'Grammar' }).first()).toBeVisible({ timeout: 30000 });
  await expect(page.getByTestId('level-band')).not.toHaveText('—');
  expect(Number(await page.getByTestId('level-score').textContent())).not.toBeNaN();
  await page.screenshot({ path: `${SHOTS}/screen-01-issues.png`, fullPage: true });

  const before = Number(await page.getByTestId('stat-selfcorrected').textContent());
  await page.evaluate(() => window.__setFakeScreenText('He goes to school every day. The results are very good. I think this thing proves everything.'));
  await expect
    .poll(async () => Number(await page.getByTestId('stat-selfcorrected').textContent()), { timeout: 30000 })
    .toBe(before + 1); // exactly the one real fix (go→goes); untouched issues earn nothing
  await page.waitForTimeout(6000);
  await expect(page.getByTestId('stat-selfcorrected')).toHaveText(String(before + 1));
  await expect(page.getByTestId('issue-card').filter({ hasText: 'very good' })).toHaveCount(1);
  await page.screenshot({ path: `${SHOTS}/screen-02-selfcorrected.png`, fullPage: true });
});

test('typed text: hint ladder never shows the fix before Reveal', async ({ page }) => {
  await page.goto('./');
  await page.getByTestId('tab-type').click();
  await page.getByTestId('textarea-input').fill('He go to school every day and he like it. ');
  const card = page.getByTestId('issue-card').filter({ hasText: 'Grammar' }).first();
  await expect(card).toBeVisible({ timeout: 30000 });
  await expect(page.getByTestId('suggestion')).toHaveCount(0);
  await expect(card).not.toContainText('goes');
  await page.screenshot({ path: `${SHOTS}/ladder-1-nudge.png` });

  await card.getByTestId('hint-rule-btn').click();
  await expect(card.getByTestId('rule-name')).toBeVisible();
  await expect(page.getByTestId('suggestion')).toHaveCount(0);
  await expect(card).not.toContainText('goes');
  await page.screenshot({ path: `${SHOTS}/ladder-2-rule.png` });

  await card.getByTestId('reveal-btn').click();
  await expect(card.getByTestId('suggestion')).toBeVisible();
  await expect(page.getByTestId('stat-revealed')).toHaveText('1');
  await page.screenshot({ path: `${SHOTS}/ladder-3-reveal.png` });
});

test('typed text: meter moves from Kindergarten to university level', async ({ page }) => {
  await page.goto('./');
  await page.getByTestId('tab-type').click();
  await page.getByTestId('textarea-input').fill(K + ' ');
  await expect(page.getByTestId('level-band')).toHaveText(/Kindergarten|Grade [12]$/, { timeout: 30000 });
  await page.getByTestId('textarea-input').fill(ACADEMIC + ' ');
  await expect(page.getByTestId('level-band')).toHaveText(/Undergraduate|Master's|PhD/, { timeout: 30000 });
  await expect(page.getByTestId('dim-vocabulary')).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/meter-academic.png`, fullPage: true });
});

test('privacy: no request leaves the origin or carries the text; nothing typed is stored', async ({ page }) => {
  const requests = [];
  page.on('request', (r) => requests.push({ url: r.url(), body: r.postData() || '' }));
  await page.goto('./');
  await page.getByTestId('tab-type').click();
  const sentinel = 'Zebrafish quixotically whistle';
  await page.getByTestId('textarea-input').fill(`${sentinel} because their is many reason. `);
  await expect(page.getByTestId('issue-card').first()).toBeVisible({ timeout: 30000 });
  await page.evaluate(() => window.__englishCoach.store.save());
  for (const r of requests) {
    expect(r.url.startsWith('http://localhost:4173/') || r.url.startsWith('blob:') || r.url.startsWith('data:'), r.url).toBe(true);
    expect(r.url + r.body).not.toContain('Zebrafish');
  }
  const ls = await page.evaluate(() => JSON.stringify(localStorage));
  expect(ls).not.toContain('Zebrafish');
});
