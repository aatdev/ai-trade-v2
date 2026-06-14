"""Unit tests for check_ib_connection.py (no network, stdlib only)."""

import json
from pathlib import Path

import check_ib_connection as cic


def test_bool_env_defaults(monkeypatch):
    monkeypatch.delenv("IB_PAPER_TRADING", raising=False)
    assert cic.bool_env("IB_PAPER_TRADING", default=True) is True
    assert cic.bool_env("IB_PAPER_TRADING", default=False) is False


def test_bool_env_truthy_tokens(monkeypatch):
    for token in ("true", "TRUE", "1", "yes", "on", " On "):
        monkeypatch.setenv("IB_X", token)
        assert cic.bool_env("IB_X") is True
    for token in ("false", "0", "no", "", "off"):
        monkeypatch.setenv("IB_X", token)
        assert cic.bool_env("IB_X") is False


def test_load_config_paper_default():
    config = cic.load_config(env={})
    assert config["paper"] is True
    assert config["headless"] is False
    assert config["read_only"] is False
    assert config["has_flex_token"] is False


def test_load_config_live_headless_readonly():
    config = cic.load_config(
        env={
            "IB_PAPER_TRADING": "false",
            "IB_HEADLESS_MODE": "true",
            "IB_READ_ONLY_MODE": "true",
            "IB_USERNAME": "demo_user",
            "IB_PASSWORD_AUTH": "secret",  # pragma: allowlist secret
            "IB_FLEX_TOKEN": "tok",
        }
    )
    assert config["paper"] is False
    assert config["headless"] is True
    assert config["read_only"] is True
    assert config["username"] == "demo_user"
    assert config["has_password"] is True
    assert config["has_flex_token"] is True


def test_load_config_restores_environment(monkeypatch):
    monkeypatch.setenv("IB_PAPER_TRADING", "false")
    cic.load_config(env={"IB_PAPER_TRADING": "true"})
    # The injected env must not leak into the real process environment.
    import os

    assert os.environ.get("IB_PAPER_TRADING") == "false"


def test_describe_mode():
    config = cic.load_config(env={})
    text = cic.describe_mode(config)
    assert "PAPER TRADING" in text
    assert "browser auth" in text
    assert "trading enabled" in text

    config2 = cic.load_config(
        env={
            "IB_PAPER_TRADING": "false",
            "IB_HEADLESS_MODE": "true",
            "IB_READ_ONLY_MODE": "true",
            "IB_FLEX_TOKEN": "tok",
        }
    )
    text2 = cic.describe_mode(config2)
    assert "LIVE TRADING" in text2
    assert "headless" in text2
    assert "read-only" in text2
    assert "Flex token set" in text2


def test_candidate_runtime_dirs_priority(monkeypatch):
    monkeypatch.delenv("IB_GATEWAY_RUNTIME_DIR", raising=False)
    dirs = cic.candidate_runtime_dirs("/tmp/explicit")
    assert dirs[0] == Path("/tmp/explicit")
    # cwd and home candidates are appended and end with the runtime subpath.
    assert any(str(d).endswith("ib-gateway/.runtime") for d in dirs)


def test_candidate_runtime_dirs_env_override(monkeypatch):
    monkeypatch.setenv("IB_GATEWAY_RUNTIME_DIR", "/var/ibgw")
    dirs = cic.candidate_runtime_dirs(None)
    assert Path("/var/ibgw") in dirs


def test_candidate_runtime_dirs_dedupes(monkeypatch):
    monkeypatch.delenv("IB_GATEWAY_RUNTIME_DIR", raising=False)
    dirs = cic.candidate_runtime_dirs(None)
    assert len(dirs) == len(set(dirs))


def test_find_session_file(tmp_path):
    runtime = tmp_path / "ib-gateway" / ".runtime"
    runtime.mkdir(parents=True)
    assert cic.find_session_file([runtime]) is None
    session = runtime / cic.SESSION_FILENAME
    session.write_text("{}", encoding="utf-8")
    assert cic.find_session_file([tmp_path, runtime]) == session


def test_load_session_roundtrip(tmp_path):
    path = tmp_path / cic.SESSION_FILENAME
    payload = {"pid": 123, "port": 5000, "version": "10.30"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert cic.load_session(path) == payload


def test_auth_status_url():
    assert cic.auth_status_url(5000) == "https://localhost:5000/v1/api/iserver/auth/status"
