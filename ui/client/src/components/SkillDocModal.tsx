import { useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useSkillDoc } from '../api';
import { Empty, ErrorNote, Loading, Modal } from './ui';

/** Strip YAML frontmatter so the rendered doc starts at the first heading. */
function stripFrontmatter(md: string): string {
  return md.replace(/^---\n[\s\S]*?\n---\n/, '');
}

/** Renders a skill's bundled markdown docs (SKILL.md + references) in a modal. */
export default function SkillDocModal({
  skill,
  title,
  onClose,
}: {
  skill: string;
  title?: string;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useSkillDoc(skill);
  const [active, setActive] = useState<string | null>(null);
  const docs = data?.docs ?? [];
  const activeName = active ?? docs[0]?.name ?? null;
  const activeDoc = docs.find((d) => d.name === activeName);

  return (
    <Modal
      title={`📖 ${title ?? skill}`}
      onClose={onClose}
      footer={<button onClick={onClose}>Закрыть</button>}
    >
      {isLoading ? (
        <Loading />
      ) : error ? (
        <ErrorNote error={error} />
      ) : docs.length === 0 ? (
        <Empty>Документация не найдена.</Empty>
      ) : (
        <>
          {docs.length > 1 ? (
            <div className="tabs">
              {docs.map((d) => (
                <button
                  key={d.name}
                  className={`tab ${d.name === activeName ? 'active' : ''}`}
                  onClick={() => setActive(d.name)}
                  title={d.name}
                >
                  {d.name.replace(/^references\//, '').replace(/\.md$/, '')}
                </button>
              ))}
            </div>
          ) : null}
          <div className="md">
            {activeDoc ? (
              <Markdown remarkPlugins={[remarkGfm]}>{stripFrontmatter(activeDoc.content)}</Markdown>
            ) : null}
          </div>
        </>
      )}
    </Modal>
  );
}
