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


# --------------------------------------------------------------------------- #
# Length handling: long text must not be jammed into a 1024-char file caption
# --------------------------------------------------------------------------- #
def test_split_text_short_is_single_chunk():
    assert st._split_text("hello", limit=100) == ["hello"]


def test_split_text_respects_limit_and_preserves_content():
    text = "\n".join(f"line {i}" for i in range(50))
    chunks = st._split_text(text, limit=40)
    assert len(chunks) > 1
    assert all(len(c) <= 40 for c in chunks)
    assert "".join(chunks) == text  # keepends -> lossless reassembly


def test_split_text_hard_splits_an_overlong_line():
    assert st._split_text("x" * 25, limit=10) == ["x" * 10, "x" * 10, "x" * 5]


def test_plan_short_message_with_file_uses_caption():
    reqs = st.plan_requests("hi", "/tmp/a.json", caption_limit=1024, text_limit=4096)
    assert reqs == [{"kind": "document", "file": "/tmp/a.json", "caption": "hi"}]


def test_plan_long_message_with_file_sends_text_then_uncaptioned_file():
    msg = "y" * 1500  # over the 1024 caption limit
    reqs = st.plan_requests(msg, "/tmp/a.json", caption_limit=1024, text_limit=4096)
    assert reqs[0] == {"kind": "message", "text": msg}  # fits in one sendMessage
    assert reqs[-1] == {"kind": "document", "file": "/tmp/a.json", "caption": None}


def test_plan_file_only_has_no_caption():
    assert st.plan_requests(None, "/tmp/a.json") == [
        {"kind": "document", "file": "/tmp/a.json", "caption": None}
    ]


def test_plan_message_only_chunks_over_text_limit():
    msg = "z" * 9000
    reqs = st.plan_requests(msg, None, text_limit=4096)
    assert [r["kind"] for r in reqs] == ["message", "message", "message"]
    assert all(len(r["text"]) <= 4096 for r in reqs)
    assert "".join(r["text"] for r in reqs) == msg
