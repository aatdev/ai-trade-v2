---
name: save-screener-results-to-mynotes
description: User wants screener results saved to MyNotes personal knowledge base
metadata: 
  node_type: memory
  type: feedback
  originSessionId: cd0f4467-d812-4034-a95a-f782ab790cf3
---

When a screener produces results (VCP, CANSLIM, dividend, earnings, PEAD, parabolic-short, etc.), save the result into the user's personal MyNotes knowledge base using the `save-note` skill.

The MyNotes base directory is configurable per user and is **not stored in git**. Resolution order (canonical block lives in `skills/save-note/SKILL.md`, updated 2026-06-05): `MYNOTES_DIR` env var → `MYNOTES_DIR=...` parsed from `./.envrc` then git-root `.envrc` (direnv hook is NOT active in Claude Code bash sessions, so the file must be read directly) → fallback `~/Documents/MyNotes`. In claude-trading-skills the gitignored `.envrc` sets `MYNOTES_DIR=/Volumes/share/reports` (SMB NAS share, finance-notes base that syncs with `~/Documents/MyNotes/Финансы`); if the volume is unmounted, warn and ask before falling back.

**Why:** The user keeps a personal MyNotes knowledge base and wants screener outputs preserved there for later reuse, not just left in the repo's `reports/` directory. The path differs per person, so it must come from the environment, not from committed files.

**How to apply:** After running any screener and presenting results, invoke the `save-note` skill to file the result into the MyNotes base dir (it auto-picks category/subcategory). The repo `reports/` output still happens as usual; MyNotes is the additional personal archive.
