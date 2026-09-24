#!/usr/bin/env python3
"""Pre-commit hook: validate SKILL.md YAML frontmatter.

Ensures every skills/*/SKILL.md has:
  - A `name` field matching its parent directory name
  - A non-empty `description` field
"""

import re
import sys
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
FIELD_RE = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)


def parse_frontmatter(text: str) -> dict[str, str]:
    """Extract YAML frontmatter key-value pairs (simple flat YAML only)."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    return dict(FIELD_RE.findall(match.group(1)))


def _strict_yaml_error(text: str) -> str | None:
    """Parse the frontmatter with a real YAML parser (the skill reviewer and
    packagers do); the flat regex above accepts `description: a: b`, which
    PyYAML rejects. Skipped when PyYAML is not installed."""
    try:
        import yaml
    except ImportError:
        return None
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return str(exc).splitlines()[0]
    if not isinstance(data, dict):
        return "frontmatter is not a mapping"
    return None


def check_skill_md(filepath: str) -> list[str]:
    """Validate a single SKILL.md file. Return list of error messages."""
    errors = []
    path = Path(filepath)

    # Only check files matching skills/*/SKILL.md
    if path.name != "SKILL.md" or path.parent.parent.name != "skills":
        return []

    expected_name = path.parent.name

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"  {filepath}: cannot read file: {e}"]

    fm = parse_frontmatter(text)

    if not fm:
        errors.append(f"  {filepath}: missing YAML frontmatter (---)")
        return errors

    strict_error = _strict_yaml_error(text)
    if strict_error:
        errors.append(
            f"  {filepath}: frontmatter is not valid YAML ({strict_error}) — "
            "quote the value (e.g. description: '...') when it contains ': '"
        )
        return errors

    name = fm.get("name", "").strip().strip("'\"")
    if not name:
        errors.append(f"  {filepath}: missing 'name' field in frontmatter")
    elif name != expected_name:
        errors.append(f"  {filepath}: name '{name}' does not match directory '{expected_name}'")

    desc = fm.get("description", "").strip().strip("'\"")
    if not desc:
        errors.append(f"  {filepath}: missing or empty 'description' field")

    return errors


def main() -> int:
    filenames = sys.argv[1:]
    all_errors: list[str] = []

    for filepath in filenames:
        all_errors.extend(check_skill_md(filepath))

    if all_errors:
        print("ERROR: SKILL.md frontmatter validation failed:")
        print("\n".join(all_errors))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
