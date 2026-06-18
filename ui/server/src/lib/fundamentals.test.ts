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
