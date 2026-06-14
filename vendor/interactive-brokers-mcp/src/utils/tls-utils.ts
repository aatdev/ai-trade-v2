// TLS verification policy for talking to the IB Gateway.
//
// The bundled IB Gateway listens on localhost with a self-signed certificate,
// so certificate verification has to be disabled for that local connection.
// Upstream hard-coded `rejectUnauthorized: false` everywhere, which means that
// if someone points IB_GATEWAY_HOST at a *remote* host the client would happily
// talk to an unverified TLS endpoint (a silent MITM hole). These helpers scope
// the "accept self-signed" behaviour to local hosts only and verify everything
// else.

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "0.0.0.0"]);

export function isLocalHost(host: string | undefined | null): boolean {
  if (!host) {
    // No host configured -> the Gateway is launched/contacted locally.
    return true;
  }
  const normalized = host.trim().toLowerCase().replace(/^\[/, "").replace(/\]$/, "");
  return LOCAL_HOSTS.has(normalized) || normalized.endsWith(".localhost");
}

/**
 * Returns the value to use for an https.Agent's `rejectUnauthorized`.
 * `false` (accept self-signed) for local hosts, `true` (verify) otherwise.
 */
export function rejectUnauthorizedFor(host: string | undefined | null): boolean {
  return !isLocalHost(host);
}
