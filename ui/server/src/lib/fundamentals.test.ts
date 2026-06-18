import { describe, expect, it } from 'vitest';
import { parseDescriptionHtml, parseFundamentals } from './fundamentals';

describe('parseDescriptionHtml', () => {
  const ldBlock = (obj: unknown) =>
    `<script type="application/ld+json">${JSON.stringify(obj)}</script>`;

  it('extracts the description from a FinancialProduct JSON-LD block', () => {
    const html =
      '<head>' +
      ldBlock({ '@type': 'BreadcrumbList', itemListElement: [] }) +
      ldBlock({
        '@context': 'https://schema.org',
        '@type': 'FinancialProduct',
        name: 'Apple Inc.',
        description: 'Apple Inc. engages in the design and sale of consumer electronics.',
      }) +
      '</head>';
    expect(parseDescriptionHtml(html)).toBe(
      'Apple Inc. engages in the design and sale of consumer electronics.',
    );
  });

  it('returns null when no FinancialProduct block carries a description', () => {
    expect(parseDescriptionHtml('<html><body>no json-ld here</body></html>')).toBeNull();
    expect(parseDescriptionHtml(ldBlock({ '@type': 'Organization', description: 'x' }))).toBeNull();
  });

  it('skips malformed JSON-LD blocks without throwing', () => {
    const html =
      '<script type="application/ld+json">{ not valid json </script>' +
      ldBlock({ '@type': 'FinancialProduct', description: 'recovered after a bad block' });
    expect(parseDescriptionHtml(html)).toBe('recovered after a bad block');
  });
});

describe('parseFundamentals description field', () => {
  it('maps business_description into data.description', () => {
    const res = parseFundamentals(
      { description: 'Acme Corp.', business_description: 'Acme makes anvils.' },
      'NYSE:ACME',
      'fixture',
    );
    expect(res.ok).toBe(true);
    expect(res.data?.name).toBe('Acme Corp.');
    expect(res.data?.description).toBe('Acme makes anvils.');
  });

  it('leaves description null when the scanner omits every description field', () => {
    const res = parseFundamentals({ description: 'Acme Corp.', sector: 'Industrials' }, 'NYSE:ACME', 'live');
    expect(res.ok).toBe(true);
    expect(res.data?.description).toBeNull();
  });
});

describe('parseFundamentals extended-hours quote', () => {
  it('maps premarket fields into the premarket quote', () => {
    const res = parseFundamentals(
      {
        description: 'Apple Inc.',
        close: 295.95,
        premarket_close: 297.47,
        premarket_change: 0.51,
        premarket_volume: 65409,
      },
      'NASDAQ:AAPL',
      'live',
    );
    expect(res.ok).toBe(true);
    expect(res.premarket).toEqual({ price: 297.47, changePct: 0.51, volume: 65409 });
    expect(res.postmarket).toBeNull();
  });

  it('maps postmarket fields when premarket is absent', () => {
    const res = parseFundamentals(
      { description: 'Apple Inc.', close: 295.95, postmarket_close: 296.1, postmarket_change: 0.05 },
      'NASDAQ:AAPL',
      'live',
    );
    expect(res.ok).toBe(true);
    expect(res.premarket).toBeNull();
    expect(res.postmarket).toEqual({ price: 296.1, changePct: 0.05, volume: null });
  });

  it('leaves both null when no extended price is present', () => {
    const res = parseFundamentals({ description: 'Apple Inc.', close: 295.95 }, 'NASDAQ:AAPL', 'live');
    expect(res.ok).toBe(true);
    expect(res.premarket).toBeNull();
    expect(res.postmarket).toBeNull();
  });
});
