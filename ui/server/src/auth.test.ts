import path from 'node:path';
import request from 'supertest';
import { describe, expect, it } from 'vitest';
import { createApp } from './app';
import { issueToken, makeAuthConfig, SESSION_COOKIE, verifyToken } from './auth';

const FIXTURE = path.resolve(process.cwd(), 'test/fixture');
const ROOT = path.resolve(process.cwd());

const ENABLED = makeAuthConfig({ user: 'trader', password: 's3cret' });
const appAuth = createApp({ dataDir: FIXTURE, projectRoot: ROOT, auth: ENABLED });
const appOpen = createApp({ dataDir: FIXTURE, projectRoot: ROOT, auth: makeAuthConfig({}) });

describe('token sign/verify', () => {
  it('round-trips a valid token', () => {
    expect(verifyToken(issueToken(ENABLED), ENABLED)).toBe('trader');
  });

  it('rejects expired, tampered, and foreign-secret tokens', () => {
    const now = 1_000_000;
    const tok = issueToken(ENABLED, now);
    expect(verifyToken(tok, ENABLED, now + ENABLED.ttlMs + 1)).toBeNull(); // expired
    expect(verifyToken(tok.slice(0, -2) + 'ff', ENABLED, now)).toBeNull(); // tampered mac
    const other = makeAuthConfig({ user: 'trader', password: 'different' });
    expect(verifyToken(tok, other, now)).toBeNull(); // password changed ⇒ key changed
    expect(verifyToken(undefined, ENABLED)).toBeNull();
    expect(verifyToken('garbage', ENABLED)).toBeNull();
  });
});

describe('auth disabled (no UI_AUTH_* set)', () => {
  it('reports auth not required and lets protected routes through', async () => {
    const status = await request(appOpen).get('/api/auth');
    expect(status.body).toEqual({ authRequired: false, authenticated: true });
    expect((await request(appOpen).get('/api/dates')).status).toBe(200);
  });
});

describe('auth enabled', () => {
  it('GET /api/auth reports required + unauthenticated without a cookie', async () => {
    const res = await request(appAuth).get('/api/auth');
    expect(res.body).toEqual({ authRequired: true, authenticated: false });
  });

  it('keeps /api/health public', async () => {
    expect((await request(appAuth).get('/api/health')).status).toBe(200);
  });

  it('blocks protected routes with 401 when unauthenticated', async () => {
    const res = await request(appAuth).get('/api/dates');
    expect(res.status).toBe(401);
    expect(res.body.ok).toBe(false);
  });

  it('rejects bad credentials with 401 and sets no cookie', async () => {
    const res = await request(appAuth).post('/api/login').send({ username: 'trader', password: 'nope' });
    expect(res.status).toBe(401);
    expect(res.headers['set-cookie']).toBeUndefined();
  });

  it('accepts good credentials, sets an httpOnly cookie, and unlocks the API', async () => {
    const agent = request.agent(appAuth);
    const login = await agent.post('/api/login').send({ username: 'trader', password: 's3cret' });
    expect(login.status).toBe(200);
    expect(login.body.ok).toBe(true);
    const cookie = String(login.headers['set-cookie'][0]);
    expect(cookie).toContain(SESSION_COOKIE);
    expect(cookie.toLowerCase()).toContain('httponly');

    // The agent now carries the session cookie.
    expect((await agent.get('/api/auth')).body).toMatchObject({ authenticated: true, user: 'trader' });
    expect((await agent.get('/api/dates')).status).toBe(200);

    // Logout clears it and re-locks the API.
    expect((await agent.post('/api/logout')).status).toBe(200);
    expect((await agent.get('/api/dates')).status).toBe(401);
  });
});
