import fs from 'node:fs';
import path from 'node:path';
import { Router } from 'express';
import { listDir, readText } from '../lib/files';
import type {
  DocSectionResponse,
  DocsIndexResponse,
  SkillDocResponse,
  SkillDocSection,
  TradingPlanResponse,
} from '@shared/types';

// Skill names are directory-safe slugs (no slashes/dots) — blocks path traversal.
const SKILL_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

// UI-bundled Russian translations (committed under ui/server/content/). Resolves
// to ui/server/content both in dev (src/routes) and prod (dist/routes).
const CONTENT_DIR = path.resolve(__dirname, '..', '..', 'content');

/**
 * Documentation sections for the "📚 Документация" modal, in sidebar order.
 * Every entry maps to a markdown file under CONTENT_DIR. `group` clusters
 * entries in the sidebar; `file` is server-controlled (never user input), so
 * there is no path-traversal surface — an unknown :id simply 404s.
 *
 * This mirrors the skills/scripts referenced by the trading plan so a reader
 * can learn every moving part of the plan from one place.
 */
interface DocManifestEntry {
  id: string;
  title: string;
  group: string;
  file: string;
}

const DOC_SECTIONS: DocManifestEntry[] = [
  // The plan itself — first section.
  { id: 'trading-plan', title: '📋 Торговый план', group: 'Обзор', file: 'trading-plan.md' },

  // Orchestration / automation that drives the whole schedule.
  { id: 'run-trading-autopilot', title: 'Автопилот (каждые 15 мин)', group: 'Автоматика', file: 'run-trading-autopilot.ru.md' },
  { id: 'run-trading-schedule', title: 'Слот-раннер расписания', group: 'Автоматика', file: 'run-trading-schedule.ru.md' },
  { id: 'workflows', title: 'Воркфлоу (regime-daily, memory-loop)', group: 'Автоматика', file: 'workflows.ru.md' },

  // Market regime + the exposure gate.
  { id: 'market-breadth-analyzer', title: 'Market Breadth Analyzer', group: 'Режим рынка и гейт', file: 'market-breadth-analyzer.ru.md' },
  { id: 'uptrend-analyzer', title: 'Uptrend Analyzer', group: 'Режим рынка и гейт', file: 'uptrend-analyzer.ru.md' },
  { id: 'exposure-coach', title: 'Exposure Coach (гейт)', group: 'Режим рынка и гейт', file: 'exposure-coach.ru.md' },
  { id: 'market-top-detector', title: 'Market Top Detector', group: 'Режим рынка и гейт', file: 'market-top-detector.ru.md' },
  { id: 'macro-regime-detector', title: 'Macro Regime Detector', group: 'Режим рынка и гейт', file: 'macro-regime-detector.ru.md' },
  { id: 'ibd-distribution-day-monitor', title: 'IBD Distribution Day Monitor', group: 'Режим рынка и гейт', file: 'ibd-distribution-day-monitor.ru.md' },
  { id: 'ftd-detector', title: 'FTD Detector', group: 'Режим рынка и гейт', file: 'ftd-detector.ru.md' },

  // Candidate discovery.
  { id: 'tradingview-screener', title: 'TradingView Screener', group: 'Поиск кандидатов', file: 'tradingview-screener.ru.md' },
  { id: 'vcp-screener', title: 'VCP Screener (лонг)', group: 'Поиск кандидатов', file: 'vcp-screener.ru.md' },
  { id: 'swing-short-screener', title: 'Swing Short Screener (шорт)', group: 'Поиск кандидатов', file: 'swing-short-screener.ru.md' },

  // Per-ticker analysis.
  { id: 'ticker-analysis', title: 'Ticker Analysis', group: 'Разбор тикера', file: 'ticker-analysis.ru.md' },
  { id: 'technical-analyst', title: 'Technical Analyst', group: 'Разбор тикера', file: 'technical-analyst.ru.md' },
  { id: 'earnings-calendar', title: 'Earnings-календарь (TV)', group: 'Разбор тикера', file: 'earnings-calendar.ru.md' },

  // Trade planning + position sizing.
  { id: 'breakout-trade-planner', title: 'Breakout Trade Planner', group: 'План сделки и риск', file: 'breakout-trade-planner.ru.md' },
  { id: 'position-sizer', title: 'Position Sizer', group: 'План сделки и риск', file: 'position-sizer.ru.md' },

  // Memory / journal.
  { id: 'trader-memory-core', title: 'Trader Memory Core', group: 'Память и журнал', file: 'trader-memory-core.ru.md' },

  // Execution + alerts.
  { id: 'portfolio-manager', title: 'Portfolio Manager (Alpaca)', group: 'Исполнение и алерты', file: 'portfolio-manager.ru.md' },
  { id: 'ib-portfolio-manager', title: 'IB Portfolio Manager (Interactive Brokers)', group: 'Исполнение и алерты', file: 'ib-portfolio-manager.ru.md' },
  { id: 'signals-alerts', title: 'Signals Alerts (TradingView)', group: 'Исполнение и алерты', file: 'signals-alerts.ru.md' },
];

const DOC_BY_ID = new Map(DOC_SECTIONS.map((s) => [s.id, s]));

export function docsRouter(projectRoot: string): Router {
  const r = Router();
  const skillsRoot = path.join(projectRoot, 'skills');

  // Ordered list of sections (no content) for the Documentation modal sidebar.
  r.get('/docs', (_req, res) => {
    const body: DocsIndexResponse = {
      sections: DOC_SECTIONS.map(({ id, title, group }) => ({ id, title, group })),
    };
    return res.json(body);
  });

  // One documentation section's markdown. `file` comes from the manifest, never
  // from the request, so a bad :id just misses the map and 404s.
  r.get('/docs/:id', (req, res) => {
    const entry = DOC_BY_ID.get(req.params.id);
    if (!entry) return res.status(404).json({ error: 'unknown doc section' });
    const content = readText(path.join(CONTENT_DIR, entry.file));
    if (content == null) return res.status(404).json({ error: `content for ${entry.id} not found` });
    const body: DocSectionResponse = { id: entry.id, title: entry.title, group: entry.group, content };
    return res.json(body);
  });

  r.get('/trading-plan', (_req, res) => {
    const content = readText(path.join(CONTENT_DIR, 'trading-plan.md'));
    if (content == null) return res.status(404).json({ error: 'trading-plan.md not found' });
    const body: TradingPlanResponse = { content };
    return res.json(body);
  });

  r.get('/skill-doc/:skill', (req, res) => {
    const skill = req.params.skill;
    if (!SKILL_RE.test(skill)) return res.status(400).json({ error: 'invalid skill name' });

    // Prefer a UI-bundled Russian doc when present.
    const ru = readText(path.join(CONTENT_DIR, `${skill}.ru.md`));
    if (ru != null) {
      return res.json({ skill, docs: [{ name: `${skill}.ru.md`, content: ru }] });
    }

    const dir = path.join(skillsRoot, skill);
    let isDir = false;
    try {
      isDir = fs.statSync(dir).isDirectory();
    } catch {
      isDir = false;
    }
    if (!isDir) return res.status(404).json({ error: 'skill not found' });

    const docs: SkillDocSection[] = [];
    const skillMd = readText(path.join(dir, 'SKILL.md'));
    if (skillMd != null) docs.push({ name: 'SKILL.md', content: skillMd });

    const refDir = path.join(dir, 'references');
    for (const name of listDir(refDir).filter((n) => n.toLowerCase().endsWith('.md')).sort()) {
      const content = readText(path.join(refDir, name));
      if (content != null) docs.push({ name: `references/${name}`, content });
    }

    if (docs.length === 0) return res.status(404).json({ error: 'no docs for skill' });
    const body: SkillDocResponse = { skill, docs };
    return res.json(body);
  });

  return r;
}
