import { register } from '../router.js';
import * as core from '../../core/fundamentals.js';

register('fundamentals', {
  description: 'Get stock fundamentals (valuation, income, margins, balance, cash flow)',
  options: {
    history: { type: 'boolean', description: 'Include annual/quarterly historical series' },
  },
  handler: (opts, positionals) => core.get({ symbol: positionals[0], history: !!opts.history }),
});
