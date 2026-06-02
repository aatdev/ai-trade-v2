---
name: ticker-analysis-mynotes-per-ticker-subdir
description: Save ticker analyses into MyNotes under a per-ticker subdirectory
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 54ba1839-98d6-4aa0-8d33-83df50fb8df1
---

When saving a single-ticker analysis to MyNotes, file it under `Финансы/Анализ-тикеров/<TICKER>/<YYYY-MM-DD>_<desc>.md` — i.e. a subdirectory named after the ticker (e.g. `Финансы/Анализ-тикеров/GOOG/2026-06-02_сводный-анализ.md`), not flat in `Анализ-тикеров/`.

**Why:** the user wants all reports for one ticker grouped together over time, so history per symbol is easy to scan in Obsidian.

**How to apply:** this overrides the save-note skill's strict two-level `КАТЕГОРИЯ/СУБКАТЕГОРИЯ` rule for ticker analyses — add the ticker as a third level. See [[save-screener-results-to-mynotes]] for the general MyNotes-saving preference.
