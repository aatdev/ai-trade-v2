"""Tests for skills/send-telegram/scripts/send_telegram.py (.env parsing)."""

import importlib.util
import os
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "send-telegram"
    / "scripts"
    / "send_telegram.py"
)
_spec = importlib.util.spec_from_file_location("send_telegram", _MODULE_PATH)
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)


def test_load_dotenv_handles_export_prefix(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nexport _ST_TEST_TOKEN=abc:123\n_ST_TEST_CHAT='-10042'\n")
    os.environ.pop("_ST_TEST_TOKEN", None)
    os.environ.pop("_ST_TEST_CHAT", None)
    try:
        st.load_dotenv(str(env))
        assert os.environ["_ST_TEST_TOKEN"] == "abc:123"
        assert os.environ["_ST_TEST_CHAT"] == "-10042"
    finally:
        os.environ.pop("_ST_TEST_TOKEN", None)
        os.environ.pop("_ST_TEST_CHAT", None)


def test_load_dotenv_does_not_override_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("export _ST_TEST_TOKEN=from_file\n")
    monkeypatch.setenv("_ST_TEST_TOKEN", "from_shell")
    st.load_dotenv(str(env))
    assert os.environ["_ST_TEST_TOKEN"] == "from_shell"
