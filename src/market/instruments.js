export const INSTRUMENTS = Object.freeze({
  MGC: Object.freeze({
    root: 'MGC',
    role: 'TRADED_MARKET',
    pointValue: 10,
    minTick: 0.1,
    tickValue: 1,
  }),
  DX: Object.freeze({
    root: 'DX',
    role: 'DXY_FILTER',
  }),
});

export function requireResolvedContract(contract) {
  if (!contract || typeof contract.symbol !== 'string' || contract.symbol.length === 0) {
    throw new Error('ACTIVE_CONTRACT_UNRESOLVED');
  }
  return contract;
}
