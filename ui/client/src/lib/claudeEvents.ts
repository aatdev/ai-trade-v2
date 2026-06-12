import type { JobLogLine } from '@shared/types';

function baseName(p: unknown): string {
  const s = String(p ?? '');
  const parts = s.split('/');
  return parts[parts.length - 1] || s;
}

/**
 * Turn a single job log line into a concise, human-readable progress string.
 * `claude -p --output-format stream-json` emits one JSON event per stdout line;
 * we summarize the interesting ones (session start, tool/skill use, text,
 * final result). Non-JSON lines (and our own system/stderr lines) pass through.
 * Returns null for events we intentionally drop (e.g. tool results).
 */
export function summarizeClaudeEvent(entry: JobLogLine): string | null {
  const raw = entry.line;
  if (entry.stream !== 'stdout') return raw.trim() ? raw : null;

  const s = raw.trim();
  if (!s.startsWith('{')) return s || null;

  let ev: any;
  try {
    ev = JSON.parse(s);
  } catch {
    return s;
  }

  switch (ev.type) {
    case 'system':
      return ev.subtype === 'init' ? `▶ session started (${ev.model ?? 'model ?'})` : null;
    case 'assistant': {
      const content = ev.message?.content;
      if (!Array.isArray(content)) return null;
      const parts: string[] = [];
      for (const c of content) {
        if (c?.type === 'tool_use') {
          const name = String(c.name ?? 'tool');
          let detail = name;
          if (name === 'Skill' && c.input?.command) detail = `Skill ${c.input.command}`;
          else if (name === 'Bash' && c.input?.command)
            detail = `Bash: ${String(c.input.command).slice(0, 80)}`;
          else if (['Read', 'Write', 'Edit'].includes(name) && c.input?.file_path)
            detail = `${name} ${baseName(c.input.file_path)}`;
          else if (name.startsWith('mcp__')) detail = name.replace(/^mcp__/, 'mcp:').replace(/__/g, '.');
          parts.push(`🔧 ${detail}`);
        } else if (c?.type === 'text' && typeof c.text === 'string' && c.text.trim()) {
          parts.push(`💬 ${c.text.trim().replace(/\s+/g, ' ').slice(0, 140)}`);
        }
      }
      return parts.length ? parts.join(' · ') : null;
    }
    case 'user':
      return null; // tool results — too noisy for a progress view
    case 'result': {
      const dur = ev.duration_ms ? `${Math.round(ev.duration_ms / 1000)}s` : '';
      const turns = ev.num_turns != null ? `${ev.num_turns} turns` : '';
      const tail = [turns, dur].filter(Boolean).join(', ');
      return `✅ done${tail ? ` (${tail})` : ''}`;
    }
    default:
      return null;
  }
}
