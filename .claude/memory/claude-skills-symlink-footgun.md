---
name: claude-skills-symlink-footgun
description: .claude/skills is a symlink to ../skills — operations on .claude/skills/<x> hit the real source
metadata: 
  node_type: memory
  type: project
  originSessionId: 2eb1329e-d5bd-49f6-b4a6-645745fc3819
---

In the claude-trading-skills repo, `.claude/skills` is a **symlink** to `../skills` (the repo's real source skills dir). So `.claude/skills/<name>` and `skills/<name>` are the SAME files.

**Why:** `rm -rf .claude/skills/<name>` deletes the real source skill (it resolves through the symlink). This happened once — `rm -rf .claude/skills/market-news-analyst` wiped `skills/market-news-analyst/`. Recovered via `git checkout HEAD -- skills/...` (the harness had committed prior work) but uncommitted edits were re-applied by hand.

**How to apply:** Never `rm`/`cp` to "sync" the installed copy — there is nothing to sync, it's the same directory. Edit `skills/<name>/` directly; the running session sees it immediately. Treat any `.claude/skills/...` path as equivalent to `skills/...`. Related: [[vendored-tv-data-layer]].
