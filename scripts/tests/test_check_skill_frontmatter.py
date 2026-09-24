"""Tests for the skill-frontmatter pre-commit hook."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "hooks" / "check_skill_frontmatter.py"
_spec = importlib.util.spec_from_file_location("check_skill_frontmatter", _PATH)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


def _skill(tmp_path, body):
    d = tmp_path / "skills" / "demo-skill"
    d.mkdir(parents=True)
    f = d / "SKILL.md"
    f.write_text(body, encoding="utf-8")
    return str(f)


def test_valid_frontmatter_passes(tmp_path):
    f = _skill(tmp_path, "---\nname: demo-skill\ndescription: Does a thing.\n---\n# X\n")
    assert hook.check_skill_md(f) == []


def test_unquoted_colon_in_description_is_rejected(tmp_path):
    # Strict YAML parsers (the skill reviewer, packaging) reject "key: a: b".
    f = _skill(
        tmp_path,
        "---\nname: demo-skill\ndescription: Screen for X — Two layers: fundamental and flow.\n---\n",
    )
    errors = hook.check_skill_md(f)
    assert errors and "YAML" in errors[0]
