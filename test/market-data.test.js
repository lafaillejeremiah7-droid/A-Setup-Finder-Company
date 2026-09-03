import test from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeTradovateBar,
  hasExecutedSideVolume,
  barDelta,
  barDeltaPct,
} from '../src/market/normalizeTradovateBar.js';
import { INSTRUMENTS, requireResolvedContract } from '../src/market/instruments.js';

test('normalizes Tradovate bar and calculates executed-side delta', () => {
  const bar = normalizeTradovateBar({
    timestamp: '2026-09-02T20:00:00.000Z',
    open: 4400,
    high: 4403,
    low: 4398,
    close: 4401,
    bidVolume: 120,
    offerVolume: 180,
  });

  assert.equal(hasExecutedSideVolume(bar), true);
  assert.equal(barDelta(bar), 60);
  assert.equal(barDeltaPct(bar), 0.2);
});

test('missing bid/offer volume disables order-flow calculation instead of guessing', () => {
  const bar = normalizeTradovateBar({
    timestamp: '2026-09-02T20:00:00.000Z',
    open: 4400,
    high: 4403,
    low: 4398,
    close: 4401,
  });

  assert.equal(hasExecutedSideVolume(bar), false);
  assert.equal(barDelta(bar), null);
  assert.equal(barDeltaPct(bar), null);
});

test('rejects malformed OHLC', () => {
  assert.throws(() => normalizeTradovateBar({
    timestamp: '2026-09-02T20:00:00.000Z',
    open: 4400,
    high: 4399,
    low: 4398,
    close: 4401,
  }), /INVALID_HIGH/);
});

test('MGC contract constants match strategy risk math', () => {
  assert.equal(INSTRUMENTS.MGC.pointValue, 10);
  assert.equal(INSTRUMENTS.MGC.minTick, 0.1);
  assert.equal(INSTRUMENTS.MGC.tickValue, 1);
});

test('DX is explicitly the DXY filter source', () => {
  assert.equal(INSTRUMENTS.DX.root, 'DX');
  assert.equal(INSTRUMENTS.DX.role, 'DXY_FILTER');
});

test('requires a resolved active contract before strategy use', () => {
  assert.throws(() => requireResolvedContract(null), /ACTIVE_CONTRACT_UNRESOLVED/);
  assert.equal(requireResolvedContract({ symbol: 'MGCV6' }).symbol, 'MGCV6');
});
