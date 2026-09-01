from types import SimpleNamespace

import scripts.aictrl_pre003_probe as probe


def test_chatgpt_login_status_accepts_official_codex_stderr(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="Logged in using ChatGPT\n",
        )

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    assert probe.has_chatgpt_login() is True


def test_chatgpt_login_status_rejects_api_key_stderr(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="Logged in using an API key\n",
        )

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    assert probe.has_chatgpt_login() is False
