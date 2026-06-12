import { register } from '../router.js';
import { getBarsBatch } from '../../core/bars.js';

register('bars', {
  description: 'Fast OHLCV for one or more symbols (single process, model-based readiness)',
  options: {
    count: { type: 'string', short: 'n', description: 'Bars per symbol (default 400, max 500)' },
    timeframe: { type: 'string', short: 't', description: 'Timeframe (default D)' },
  },
  handler: (opts, positionals) =>
    getBarsBatch({
      symbols: positionals,
      count: opts.count ? Number(opts.count) : undefined,
      timeframe: opts.timeframe || 'D',
    }),
});
