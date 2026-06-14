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

**How to apply (verified 2026-06-14, adding ib-portfolio-manager):** the SIMPLEST correct path is to run `python3 scripts/generate_skill_docs.py` with **NO flags** — the plain run iterates ALL skills so its `_compute_nav_orders(overwrite=False)` is byte-identical to `--check`, it writes only the brand-new pages (existing ones are skipped, never clobbered), and it updates both `index.md` tables + the EN `skill-catalog.md` API matrix. Do this AFTER `package_skills.py` so the `.skill` download button is already present (else the page drifts). Do NOT use `--skill <name>` (nav computed over a 1-element list → 11) or `--overwrite` (counts HAND_WRITTEN in numbering) — both diverge from `--check`.

Gotcha: inserting a skill mid-alphabet shifts `nav_order` +1 for every alphabetically-later `generated: true` page (older pages without a `generated:` key are hand-maintained and NOT content-checked, so only the generator-owned ones drift). The plain run won't rewrite those existing pages — regenerate them with a small "rerender-owned" loop: for each non-HAND_WRITTEN dir whose page has `_doc_is_generated(p) is True`, render via `_render_skill_pages(d, name, nav, api_reqs, cli, pkg_dir, "auto")` with `nav` from `_compute_nav_orders(all, overwrite=False)` and write `text.rstrip("\n")+"\n"`. Then `--check` is green. Title acronyms live in `_title_case` (e.g. add `"ib":"IB"`); editing it re-renders only pages with that word segment. Pipeline order for a new skill: index.yaml → `generate_catalog_from_index.py` (CLAUDE.md api-matrix + README catalogs) → `build_snapshot.py` → `package_skills.py` → plain `generate_skill_docs.py` + rerender-owned → manual catalog category sections + README hand API table + UI `docs.ts`. Also: never run `pre-commit run --all-files` with everything staged — trailing-whitespace/EOF/ruff hooks auto-edit unrelated files repo-wide (use staged-files mode). Related: [[vendored-tv-data-layer]].
