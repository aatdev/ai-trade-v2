import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import request from 'supertest';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createApp } from '../app';
import { clearListCache } from './files';
import { deleteSignal, parseSignalBlocks, signalsFile } from './signals';

const FIXTURE = path.resolve(process.cwd(), 'test/fixture');

function tmpDataDirWithSignals(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ui-signals-'));
  fs.mkdirSync(path.join(dir, 'analysis'), { recursive: true });
  fs.copyFileSync(signalsFile(FIXTURE), signalsFile(dir));
  return dir;
}

describe('parseSignalBlocks', () => {
  it('parses each ## block with date, ticker and status', () => {
    const raw = fs.readFileSync(signalsFile(FIXTURE), 'utf8');
    const blocks = parseSignalBlocks(raw);
    expect(blocks.map((b) => b.ticker)).toEqual(['ALB', 'MSFT']);
    expect(blocks[0].date).toBe('2026-06-11');
    expect(blocks[0].status).toContain('BUY');
    expect(blocks[0].markdown.startsWith('## 2026-06-11 — ALB')).toBe(true);
  });
});

describe('deleteSignal', () => {
  let dir: string;
  beforeEach(() => {
    clearListCache();
    dir = tmpDataDirWithSignals();
  });
  afterEach(() => fs.rmSync(dir, { recursive: true, force: true }));

  it('removes only the matching block and preserves the rest + preamble', () => {
    const res = deleteSignal(dir, 'ALB', '2026-06-11');
    expect(res.found).toBe(true);
    expect(res.removed).toBe(1);
    expect(res.kept).toBe(1);

    const after = parseSignalBlocks(fs.readFileSync(signalsFile(dir), 'utf8'));
    expect(after.map((b) => b.ticker)).toEqual(['MSFT']);
    expect(fs.readFileSync(signalsFile(dir), 'utf8')).toContain('# Trading Signals Journal');
  });

  it('is case-insensitive on the ticker and reports not-found otherwise', () => {
    expect(deleteSignal(dir, 'msft', '2026-06-11').removed).toBe(1);
    expect(deleteSignal(dir, 'NVDA', '2026-06-11').found).toBe(false);
    expect(deleteSignal(dir, 'ALB', '1999-01-01').found).toBe(false);
  });
});

describe('DELETE /api/signals/:ticker/:date', () => {
  let dir: string;
  beforeEach(() => {
    clearListCache();
    dir = tmpDataDirWithSignals();
  });
  afterEach(() => fs.rmSync(dir, { recursive: true, force: true }));

  it('deletes a signal and the feed reflects it', async () => {
    const app = createApp({ dataDir: dir, projectRoot: dir });
    const del = await request(app).delete('/api/signals/ALB/2026-06-11');
    expect(del.status).toBe(200);
    expect(del.body.removed).toBe(1);

    const feed = await request(app).get('/api/signals');
    expect(feed.body.signals.map((s: { ticker: string }) => s.ticker)).toEqual(['MSFT']);
  });

  it('404 when the signal is absent, 400 on bad params', async () => {
    const app = createApp({ dataDir: dir, projectRoot: dir });
    expect((await request(app).delete('/api/signals/NVDA/2026-06-11')).status).toBe(404);
    expect((await request(app).delete('/api/signals/AB$/2026-06-11')).status).toBe(400);
    expect((await request(app).delete('/api/signals/ALB/2026-6-1')).status).toBe(400);
  });
});
