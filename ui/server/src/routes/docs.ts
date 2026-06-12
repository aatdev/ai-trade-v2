import fs from 'node:fs';
import path from 'node:path';
import { Router } from 'express';
import { listDir, readText } from '../lib/files';
import type { SkillDocResponse, SkillDocSection } from '@shared/types';

// Skill names are directory-safe slugs (no slashes/dots) — blocks path traversal.
const SKILL_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

// UI-bundled Russian translations (committed under ui/server/content/). Resolves
// to ui/server/content both in dev (src/routes) and prod (dist/routes).
const CONTENT_DIR = path.resolve(__dirname, '..', '..', 'content');

export function docsRouter(projectRoot: string): Router {
  const r = Router();
  const skillsRoot = path.join(projectRoot, 'skills');

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
