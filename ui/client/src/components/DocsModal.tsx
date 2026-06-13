import { useMemo, useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { DocSectionMeta } from '@shared/types';
import { useDocSection, useDocsIndex } from '../api';
import { ErrorNote, Loading, Modal } from './ui';

/** Strip YAML frontmatter so the rendered doc starts at the first heading. */
function stripFrontmatter(md: string): string {
  return md.replace(/^---\n[\s\S]*?\n---\n/, '');
}

/** Group sections by their `group` label, preserving first-seen order. */
function groupSections(sections: DocSectionMeta[]): { group: string; items: DocSectionMeta[] }[] {
  const groups: { group: string; items: DocSectionMeta[] }[] = [];
  for (const s of sections) {
    let g = groups.find((x) => x.group === s.group);
    if (!g) {
      g = { group: s.group, items: [] };
      groups.push(g);
    }
    g.items.push(s);
  }
  return groups;
}

/**
 * Documentation browser: a left sidebar lists every section (the trading plan
 * plus one entry per skill/script the plan uses); the right pane renders the
 * selected section's markdown.
 */
export default function DocsModal({ onClose }: { onClose: () => void }) {
  const { data: index, isLoading, error } = useDocsIndex();
  const [active, setActive] = useState<string | null>(null);

  const sections = index?.sections ?? [];
  const activeId = active ?? sections[0]?.id ?? null;
  const groups = useMemo(() => groupSections(sections), [sections]);

  const { data: doc, isLoading: docLoading, error: docError } = useDocSection(activeId);

  return (
    <Modal
      title="📚 Документация"
      onClose={onClose}
      fullscreen
      footer={<button onClick={onClose}>Закрыть</button>}
    >
      {isLoading ? (
        <Loading />
      ) : error ? (
        <ErrorNote error={error} />
      ) : (
        <div className="docs-layout">
          <nav className="docs-nav">
            {groups.map((g) => (
              <div key={g.group} className="docs-nav-group">
                <div className="docs-nav-group-label">{g.group}</div>
                {g.items.map((s) => (
                  <button
                    key={s.id}
                    className={`docs-nav-item ${s.id === activeId ? 'active' : ''}`}
                    onClick={() => setActive(s.id)}
                    title={s.title}
                  >
                    {s.title}
                  </button>
                ))}
              </div>
            ))}
          </nav>
          <div className="docs-body">
            {docLoading ? (
              <Loading />
            ) : docError ? (
              <ErrorNote error={docError} />
            ) : doc ? (
              <div className="md">
                <Markdown remarkPlugins={[remarkGfm]}>{stripFrontmatter(doc.content)}</Markdown>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </Modal>
  );
}
