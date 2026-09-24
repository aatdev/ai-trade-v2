import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resolveCdpEndpoint } from '../src/cdp_config.js';

const emptyDir = () => mkdtempSync(join(tmpdir(), 'cdpcfg-'));

test('defaults to localhost:9222 when nothing is configured', () => {
  const d = emptyDir();
  assert.deepEqual(resolveCdpEndpoint({ env: {}, searchDirs: [d] }), { host: 'localhost', port: 9222 });
});

test('env vars win over .env', () => {
  const d = emptyDir();
  writeFileSync(join(d, '.env'), 'TV_CDP_HOST=1.1.1.1\nTV_CDP_PORT=1111\n');
  const env = { TV_CDP_HOST: '10.99.0.1', TV_CDP_PORT: '9222' };
  assert.deepEqual(resolveCdpEndpoint({ env, searchDirs: [d] }), { host: '10.99.0.1', port: 9222 });
});

test('falls back to the nearest .env walking up from a search dir', () => {
  const root = emptyDir();
  writeFileSync(join(root, '.env'), "# c\nexport TV_CDP_HOST='10.99.0.1'\nTV_CDP_PORT=9223\n");
  const nested = join(root, 'a', 'b');
  mkdirSync(nested, { recursive: true });
  assert.deepEqual(resolveCdpEndpoint({ env: {}, searchDirs: [nested] }), { host: '10.99.0.1', port: 9223 });
});

test('accepts host:port in TV_CDP_HOST', () => {
  const d = emptyDir();
  assert.deepEqual(resolveCdpEndpoint({ env: { TV_CDP_HOST: '10.99.0.1:9444' }, searchDirs: [d] }), { host: '10.99.0.1', port: 9444 });
});
