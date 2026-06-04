# Automation Pipelines (Skill Self-Improvement & Auto-Generation)

Detailed design notes for the two scheduled self-maintenance pipelines.
For schedules, lock files, and "the job didn't do anything" triage, see
[`maintenance-runbook.md`](maintenance-runbook.md) §5.

## Skill Self-Improvement Loop

An automated pipeline reviews and improves skill quality on a daily cadence.

**Architecture:**
- `scripts/run_skill_improvement_loop.py` — orchestrator (round-robin selection, auto scoring, Claude CLI improvement, quality gate, PR creation)
- `skills/dual-axis-skill-reviewer/scripts/run_dual_axis_review.py` — scoring engine (5-category deterministic auto axis, optional LLM axis)
- `scripts/run_skill_improvement.sh` — thin shell wrapper for launchd
- `launchd/com.trade-analysis.skill-improvement.plist` — macOS launchd agent (daily 05:00)

**Key design decisions:**
- Improvement trigger uses `auto_review.score` (deterministic) instead of `final_review.score` (LLM-influenced) for reproducibility
- Quality gate re-scores after improvement with tests enabled; rolls back if score didn't improve
- PID-based lock file with stale detection prevents concurrent runs
- Git safety checks (clean tree, main branch, `git pull --ff-only`) before any operations
- `knowledge_only` skills (no scripts, references only) get adjusted scoring to avoid unfair penalties

**Running manually:**
```bash
# Dry-run: score one skill without improvements or PRs
python3 scripts/run_skill_improvement_loop.py --dry-run

# Dry-run all skills
python3 scripts/run_skill_improvement_loop.py --dry-run --all

# Full run
python3 scripts/run_skill_improvement_loop.py
```

**Running the reviewer standalone:**
```bash
# Score a random skill
uv run skills/dual-axis-skill-reviewer/scripts/run_dual_axis_review.py \
  --project-root . --output-dir reports/

# Score a specific skill
uv run skills/dual-axis-skill-reviewer/scripts/run_dual_axis_review.py \
  --project-root . --skill backtest-expert --output-dir reports/

# Score all skills
uv run skills/dual-axis-skill-reviewer/scripts/run_dual_axis_review.py \
  --project-root . --all --output-dir reports/
```

**State and output files:**
- `logs/.skill_improvement_state.json` — round-robin state and 60-entry history
- `logs/skill_improvement.log` — execution log (30-day rotation)
- `reports/skill-improvement-log/YYYY-MM-DD_summary.md` — daily summary

**Tests:**
```bash
# Reviewer tests
python3 -m pytest skills/dual-axis-skill-reviewer/scripts/tests/ -v

# Orchestrator tests
python3 -m pytest scripts/tests/test_skill_improvement_loop.py -v
```

## Skill Auto-Generation Pipeline

An automated pipeline that mines session logs for skill ideas (weekly) and designs, reviews, and creates new skills as PRs (daily).

**Architecture:**
- `scripts/run_skill_generation_pipeline.py` — orchestrator (weekly: mine+score, daily: design+review+PR)
- `skills/skill-idea-miner/` — mining and scoring scripts
- `skills/skill-designer/` — design prompt builder with quality references
- `skills/dual-axis-skill-reviewer/` — scoring engine (reused from improvement loop)
- `scripts/run_skill_generation.sh` — thin shell wrapper for launchd
- `launchd/com.trade-analysis.skill-generation-weekly.plist` — weekly mining (Saturday 06:00)
- `launchd/com.trade-analysis.skill-generation-daily.plist` — daily generation (07:00)

**Key design decisions:**
- Weekly mode mines session logs and scores ideas into `logs/.skill_generation_backlog.yaml`
- Daily mode picks the highest-scoring eligible idea and generates a complete skill
- `select_next_idea()` prioritizes pending ideas by composite score; retries `design_failed`/`pr_failed` once
- `review_failed` is terminal (no retry) since it indicates content quality issues
- Runtime dedup checks `skills/<name>/SKILL.md` existence before processing
- `_check_unexpected_changes()` detects modifications outside `skills/<name>/` and `reports/`; preserves branch for manual inspection
- Atomic backlog updates via `tempfile` + `os.replace()`
- `created_branch` flag prevents spurious `git checkout main` in finally block

**Running manually:**
```bash
# Weekly: mine ideas from session logs and score them
python3 scripts/run_skill_generation_pipeline.py --mode weekly --dry-run

# Daily: design a skill from the highest-scoring backlog idea
python3 scripts/run_skill_generation_pipeline.py --mode daily --dry-run

# Full daily run (creates branch, designs skill, opens PR)
python3 scripts/run_skill_generation_pipeline.py --mode daily
```

**State and output files:**
- `logs/.skill_generation_state.json` — run history (60-entry limit)
- `logs/.skill_generation_backlog.yaml` — scored ideas with status tracking
- `logs/skill_generation.log` — execution log (30-day rotation)
- `reports/skill-generation-log/YYYY-MM-DD_daily.md` — daily generation summary

**Tests:**
```bash
# Pipeline tests
python3 -m pytest scripts/tests/test_skill_generation_pipeline.py -v

# Skill designer tests
python3 -m pytest skills/skill-designer/scripts/tests/ -v
```
