---
name: ib-gateway-probe-gotchas
description: Probing the bundled IB Gateway from Node — session-file location and 403-without-User-Agent
metadata: 
  node_type: memory
  type: reference
  originSessionId: 207a15f0-1532-4f59-934f-b8bce8666f35
---

Probing the bundled IB Client Portal Gateway (interactive-brokers-mcp) over its local HTTPS port from Node:

- **Session file location:** `gateway-session.json` is written under `vendor/interactive-brokers-mcp/ib-gateway/.runtime/` (NOT `<repo>/ib-gateway/.runtime` or `$HOME`). It holds the listening `port` (e.g. 5002). `check_ib_connection.py`'s `candidate_runtime_dirs` (cwd/home/`IB_GATEWAY_RUNTIME_DIR`) won't find it from the repo root — add the vendor path explicitly.
- **403 Access Denied without User-Agent:** the Gateway returns `403` to any request lacking a `User-Agent` header. Node's `https.request` sends none by default → 403. Python `urllib` works because it auto-sets `User-Agent: Python-urllib/x`. Always set a `User-Agent` header in Node probes.
- Use `host: 'localhost'` (Host-header validation). `POST` or `GET` both work on `/v1/api/iserver/auth/status` → `{"authenticated":true,...}`.

Implemented in `ui/server/src/lib/ibHealth.ts` (the `GET /api/ib/health` liveness probe driving the red "Счёт IB" tab indicator).
