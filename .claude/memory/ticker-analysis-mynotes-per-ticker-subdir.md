---
name: ticker-analysis-mynotes-per-ticker-subdir
description: Save ticker analyses into MyNotes under per-ticker, per-date subdirectories
metadata:
  node_type: memory
  type: feedback
  originSessionId: 54ba1839-98d6-4aa0-8d33-83df50fb8df1
---

When saving a single-ticker analysis to MyNotes, file it under `Финансы/Анализ-тикеров/<TICKER>/<YYYY-MM-DD>/<desc>.md` — a subdirectory named after the ticker, then a date subdirectory inside it (e.g. `Финансы/Анализ-тикеров/GOOG/2026-06-05/сводный-анализ.md`), not flat in `Анализ-тикеров/`. Since the date is in the path, file names carry no date prefix. Save **all** report files, not just the summary: `report.md` → `сводный-анализ.md`, `fundamental.md` → `фундаментал.md`, `technical.md` → `техника.md`, `news.md` → `новости.md`.

**Why:** the user wants all reports for one ticker grouped together over time, with each analysis run grouped by date (confirmed 2026-06-05: date-level grouping inside the ticker dir), so history per symbol is easy to scan in Obsidian — and confirmed (2026-06-02) they want the full set, not just the summary report.

**How to apply:** this overrides the save-note skill's strict two-level `КАТЕГОРИЯ/СУБКАТЕГОРИЯ` rule for ticker analyses — ticker is the third level, date is the fourth. The rule is now also codified in `skills/save-note/SKILL.md` itself. See [[save-screener-results-to-mynotes]] for the general MyNotes-saving preference.
