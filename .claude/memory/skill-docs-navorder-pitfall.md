---
name: skill-docs-navorder-pitfall
description: "generate_skill_docs.py --skill/--overwrite write nav_order inconsistent with --check; how to add a new skill's docs without drift"
metadata: 
  node_type: memory
  type: project
  originSessionId: 27a6953d-302f-41e3-9e78-62a88de2ee64
---

`scripts/generate_skill_docs.py` has a nav_order inconsistency (as of 2026-06-04): the `--skill <name>` path computes nav_order over the *filtered* skill list (always 11), and `--overwrite` includes HAND_WRITTEN skills in numbering, while `--check` numbers over all non-hand-written skills — so pages written by either mode can immediately fail the `skill-docs-drift` gate.

**Why:** `_compute_nav_orders(skill_dirs, overwrite)` is called with different `skill_dirs`/`overwrite` in the write vs check paths.

**How to apply:** after adding a new skill, render pages with check semantics — import the module, call `_compute_nav_orders(sorted(skills_dir.iterdir()), overwrite=False)` and `_render_skill_pages(..., mode="auto")`, write `text.rstrip("\n") + "\n"`. Re-render again after `package_skills.py` creates the `.skill` file (the page gains a download button → drift otherwise). Also: never run `pre-commit run --all-files` with everything staged — trailing-whitespace/EOF/ruff hooks auto-edit unrelated files repo-wide (use staged-files mode). Related: [[vendored-tv-data-layer]].
