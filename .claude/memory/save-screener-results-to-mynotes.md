---
name: save-screener-results-to-mynotes
description: User wants screener results saved to MyNotes personal knowledge base
metadata: 
  node_type: memory
  type: feedback
  originSessionId: cd0f4467-d812-4034-a95a-f782ab790cf3
---

When a screener produces results (VCP, CANSLIM, dividend, earnings, PEAD, parabolic-short, etc.), save the result into the user's personal MyNotes knowledge base using the `save-note` skill.

The MyNotes base directory is configurable per user and is **not stored in git**: resolve it as `${MYNOTES_DIR:-$HOME/Documents/MyNotes}` (env var first, fallback default). Users set `MYNOTES_DIR` in their shell profile, a gitignored `.envrc`, or the `env` block of `.claude/settings.local.json`.

**Why:** The user keeps a personal MyNotes knowledge base and wants screener outputs preserved there for later reuse, not just left in the repo's `reports/` directory. The path differs per person, so it must come from the environment, not from committed files.

**How to apply:** After running any screener and presenting results, invoke the `save-note` skill to file the result into the MyNotes base dir (it auto-picks category/subcategory). The repo `reports/` output still happens as usual; MyNotes is the additional personal archive.
