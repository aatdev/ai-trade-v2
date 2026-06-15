import crypto from 'node:crypto';
import { Router, type NextFunction, type Request, type Response } from 'express';
import type { AuthActionResponse, AuthStatusResponse } from '@shared/types';

/**
 * Cookie-based session auth for the dashboard.
 *
 * The server can spawn real scheduler processes, so even though it binds to
 * loopback only, an optional login gate keeps a casual local user (or anyone
 * reaching it through a tunnel / port-forward) out. Credentials live in the
 * repo `.env` (`UI_AUTH_USER` / `UI_AUTH_PASSWORD`); auth is disabled entirely
 * when either is unset, preserving the original no-login behavior.
 *
 * Sessions are stateless: the cookie carries a `v1.<user>.<exp>.<hmac>` token
 * signed with HMAC-SHA256. The signing key is `UI_AUTH_SECRET` if set, else it
 * is derived from the credentials — so changing the password invalidates every
 * outstanding session. A cookie is required (not a header) because the client
 * authenticates SSE streams (`EventSource`) and chart `<img>` requests, neither
 * of which can carry an Authorization header.
 */

export const SESSION_COOKIE = 'trading_ui_session';
const TOKEN_VERSION = 'v1';
const DEFAULT_TTL_HOURS = 168; // 7 days

export interface AuthConfig {
  enabled: boolean;
  user: string;
  password: string;
  /** HMAC signing key for session tokens. */
  secret: Buffer;
  /** Session lifetime in milliseconds. */
  ttlMs: number;
}

/** Build an auth config from explicit values (used by tests and resolveAuthConfig). */
export function makeAuthConfig(opts: {
  user?: string;
  password?: string;
  secret?: string;
  ttlMs?: number;
}): AuthConfig {
  const user = (opts.user ?? '').trim();
  const password = opts.password ?? '';
  const enabled = user.length > 0 && password.length > 0;
  const secret =
    opts.secret && opts.secret.length > 0
      ? Buffer.from(opts.secret, 'utf8')
      : crypto.createHash('sha256').update(`trading-ui\0${user}\0${password}`).digest();
  return {
    enabled,
    user,
    password,
    secret,
    ttlMs: opts.ttlMs ?? DEFAULT_TTL_HOURS * 60 * 60 * 1000,
  };
}

/** Read the auth config from the environment (loaded from `.env` at startup). */
export function resolveAuthConfig(): AuthConfig {
  const ttlHours = Number(process.env.UI_AUTH_TTL_HOURS);
  return makeAuthConfig({
    user: process.env.UI_AUTH_USER,
    password: process.env.UI_AUTH_PASSWORD,
    secret: process.env.UI_AUTH_SECRET,
    ttlMs: Number.isFinite(ttlHours) && ttlHours > 0 ? ttlHours * 60 * 60 * 1000 : undefined,
  });
}

/** Constant-time string compare that tolerates differing lengths. */
function safeEqual(a: string, b: string): boolean {
  const ha = crypto.createHash('sha256').update(a).digest();
  const hb = crypto.createHash('sha256').update(b).digest();
  return crypto.timingSafeEqual(ha, hb);
}

function b64url(s: string): string {
  return Buffer.from(s, 'utf8').toString('base64url');
}

function sign(payload: string, secret: Buffer): string {
  return crypto.createHmac('sha256', secret).update(payload).digest('hex');
}

/** Mint a session token valid until now + ttl. */
export function issueToken(cfg: AuthConfig, now = Date.now()): string {
  const exp = now + cfg.ttlMs;
  const payload = `${TOKEN_VERSION}.${b64url(cfg.user)}.${exp}`;
  return `${payload}.${sign(payload, cfg.secret)}`;
}

/** Verify a session token; returns the user name when valid, else null. */
export function verifyToken(token: string | undefined, cfg: AuthConfig, now = Date.now()): string | null {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 4 || parts[0] !== TOKEN_VERSION) return null;
  const [, userB64, expStr, mac] = parts;
  const payload = `${TOKEN_VERSION}.${userB64}.${expStr}`;
  const expected = sign(payload, cfg.secret);
  // Length-equal hex strings → timingSafeEqual is safe; mismatch ⇒ reject.
  if (mac.length !== expected.length || !crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(expected))) {
    return null;
  }
  const exp = Number(expStr);
  if (!Number.isFinite(exp) || exp <= now) return null;
  let user: string;
  try {
    user = Buffer.from(userB64, 'base64url').toString('utf8');
  } catch {
    return null;
  }
  // Reject tokens minted for a now-changed configured user.
  if (!safeEqual(user, cfg.user)) return null;
  return user;
}

/** Parse a Cookie header into a name→value map (no cookie-parser dependency). */
export function parseCookies(header: string | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!header) return out;
  for (const part of header.split(';')) {
    const eq = part.indexOf('=');
    if (eq < 0) continue;
    const name = part.slice(0, eq).trim();
    if (!name) continue;
    out[name] = decodeURIComponent(part.slice(eq + 1).trim());
  }
  return out;
}

function tokenFromReq(req: Request): string | undefined {
  return parseCookies(req.headers.cookie)[SESSION_COOKIE];
}

/**
 * Login / logout / status routes. Mount under `/api` BEFORE the gate so these
 * stay reachable while unauthenticated.
 */
export function authRouter(cfg: AuthConfig): Router {
  const r = Router();

  r.get('/auth', (req, res) => {
    const body: AuthStatusResponse = cfg.enabled
      ? (() => {
          const user = verifyToken(tokenFromReq(req), cfg);
          return user
            ? { authRequired: true, authenticated: true, user }
            : { authRequired: true, authenticated: false };
        })()
      : { authRequired: false, authenticated: true };
    res.json(body);
  });

  r.post('/login', (req, res) => {
    if (!cfg.enabled) {
      return res.json({ ok: true } satisfies AuthActionResponse);
    }
    const username = String(req.body?.username ?? '');
    const password = String(req.body?.password ?? '');
    // Always compare both fields to keep timing independent of which is wrong.
    const ok = safeEqual(username, cfg.user) && safeEqual(password, cfg.password);
    if (!ok) {
      return res.status(401).json({ ok: false, error: 'Неверный логин или пароль' } satisfies AuthActionResponse);
    }
    res.cookie(SESSION_COOKIE, issueToken(cfg), {
      httpOnly: true,
      sameSite: 'lax',
      path: '/',
      maxAge: cfg.ttlMs,
    });
    return res.json({ ok: true } satisfies AuthActionResponse);
  });

  r.post('/logout', (_req, res) => {
    res.clearCookie(SESSION_COOKIE, { path: '/' });
    res.json({ ok: true } satisfies AuthActionResponse);
  });

  return r;
}

/**
 * Gate every other `/api` route behind a valid session. Mount AFTER authRouter
 * and the public `/api/health` route. A no-op when auth is disabled.
 */
export function requireAuth(cfg: AuthConfig) {
  return (req: Request, res: Response, next: NextFunction): void => {
    if (!cfg.enabled) return next();
    if (verifyToken(tokenFromReq(req), cfg)) return next();
    res.status(401).json({ ok: false, error: 'unauthorized' });
  };
}
