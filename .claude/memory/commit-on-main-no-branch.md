---
name: commit-on-main-no-branch
description: commit directly to the current branch (main) — never create a branch for changes
metadata: 
  node_type: memory
  type: feedback
  originSessionId: f428c57e-9f02-4648-8052-d55f771df060
---

When asked to commit in this repo, commit straight to the current branch (usually `main`). Never create a new branch for changes.

**Why:** User stated this explicitly on 2026-06-15; this is a solo-operated repo where the default "branch first when on main" behavior is unwanted friction.

**How to apply:** Skip the branch-creation step entirely. Stage only the files relevant to the task at hand (the working tree often carries unrelated in-progress work — don't sweep it into the commit; surface it instead). Still end commit messages with the required Co-Authored-By trailer.
