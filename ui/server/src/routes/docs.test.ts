import path from 'node:path';
import request from 'supertest';
import { describe, expect, it } from 'vitest';
import { createApp } from '../app';

// projectRoot is irrelevant for /api/docs — content is read from the bundled
// ui/server/content dir (resolved relative to the route module), not the repo.
const app = createApp({ dataDir: path.resolve(process.cwd(), 'test/fixture'), projectRoot: process.cwd() });

describe('GET /api/docs', () => {
  it('lists sections with the trading plan first and groups', async () => {
    const res = await request(app).get('/api/docs');
    expect(res.status).toBe(200);
    const { sections } = res.body;
    expect(Array.isArray(sections)).toBe(true);
    expect(sections[0].id).toBe('trading-plan');
    // every section carries a sidebar group label and title
    expect(sections.every((s: { title: string; group: string }) => !!s.title && !!s.group)).toBe(true);
    // a representative skill section is present
    expect(sections.some((s: { id: string }) => s.id === 'ftd-detector')).toBe(true);
  });
});

describe('GET /api/docs/:id', () => {
  it('returns markdown content for a known section', async () => {
    const res = await request(app).get('/api/docs/ftd-detector');
    expect(res.status).toBe(200);
    expect(res.body.id).toBe('ftd-detector');
    expect(res.body.title).toBeTruthy();
    expect(res.body.content).toContain('FTD');
  });

  it('serves the trading plan section', async () => {
    const res = await request(app).get('/api/docs/trading-plan');
    expect(res.status).toBe(200);
    expect(res.body.content.length).toBeGreaterThan(100);
  });

  it('404s for an unknown section id', async () => {
    const res = await request(app).get('/api/docs/not-a-real-section');
    expect(res.status).toBe(404);
  });
});
