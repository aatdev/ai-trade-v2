---
name: no-test-prod-isolation
description: "User explicitly does not want tests isolated from production trading-data (logs/theses); don't propose it"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 44b17f7b-9efd-48ac-ad1c-34be4b728be2
  modified: 2026-09-24T19:38:36.603Z
---

Don't propose or implement separating tests from production `trading-data/` (e.g. monkeypatching LOG_FILE / trader-memory state-dir in conftest). User rejected it on 2026-09-24 when it was the #1 audit fix ("тест и прод делить не надо").

**Why:** user's explicit decision; reason not stated.
**How to apply:** leave test→prod log/state leakage out of fix plans; if a test side effect clearly destroys real data (e.g. invalidating real theses), mention the specific incident briefly, but don't reopen the isolation proposal. See [[trading-logic-audit-remaining-backlog]].
