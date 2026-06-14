import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { DocSectionMeta } from '@shared/types';
import { useDocSection, useDocsIndex } from '../api';
import { ErrorNote, Loading, Modal } from './ui';

/** Strip YAML frontmatter so the rendered doc starts at the first heading. */
function stripFrontmatter(md: string): string {
  return md.replace(/^---\n[\s\S]*?\n---\n/, '');
}

/** localStorage key for the per-section scroll positions (`{ sectionId: scrollTop }`). */
const SCROLL_STORAGE_KEY = 'docsModal.scrollPositions';

function loadScrollPositions(): Record<string, number> {
  try {
    const parsed = JSON.parse(localStorage.getItem(SCROLL_STORAGE_KEY) ?? 'null');
    if (parsed && typeof parsed === 'object') return parsed as Record<string, number>;
  } catch {
    // Ignore unavailable (private mode) or corrupt storage; start fresh.
  }
  return {};
}

function saveScrollPositions(positions: Record<string, number>): void {
  try {
    localStorage.setItem(SCROLL_STORAGE_KEY, JSON.stringify(positions));
  } catch {
    // Ignore write failures (quota exceeded / storage disabled).
  }
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

  // Remember the scroll position of each section so navigating back to a
  // previously-viewed section restores where the reader left off. Persisted to
  // localStorage so positions survive closing/reopening the modal and reloads.
  const bodyRef = useRef<HTMLDivElement>(null);
  const scrollPositions = useRef<Record<string, number>>();
  if (scrollPositions.current === undefined) {
    scrollPositions.current = loadScrollPositions();
  }

  function rememberScroll() {
    if (bodyRef.current && activeId) {
      scrollPositions.current![activeId] = bodyRef.current.scrollTop;
      saveScrollPositions(scrollPositions.current!);
    }
  }

  function selectSection(id: string) {
    rememberScroll();
    setActive(id);
  }

  function handleClose() {
    rememberScroll();
    onClose();
  }

  // Restore the saved scroll position once the selected section's content is
  // rendered (default to the top for sections not visited yet).
  useLayoutEffect(() => {
    if (bodyRef.current && activeId && !docLoading && doc) {
      bodyRef.current.scrollTop = scrollPositions.current![activeId] ?? 0;
    }
  }, [activeId, doc, docLoading]);

  return (
    <Modal
      title="📚 Документация"
      onClose={handleClose}
      fullscreen
      footer={<button onClick={handleClose}>Закрыть</button>}
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
                    onClick={() => selectSection(s.id)}
                    title={s.title}
                  >
                    {s.title}
                  </button>
                ))}
              </div>
            ))}
          </nav>
          <div className="docs-body" ref={bodyRef}>
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
