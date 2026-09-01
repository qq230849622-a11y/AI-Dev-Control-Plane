import scripts.aictrl_pre003_probe as probe

from aictrl.pre003 import is_luna_session


def test_session_identity_survives_empty_v01210_sessionview_model():
    # PRE-003 retained-session diagnostic evidence from the installed AO v0.12.10
    # runtime: SessionView.model can be empty for the terminated Chat session.
    session_view = {
        "session": {
            "id": "ai-dev-control-plane-42",
            "harness": "codex",
            "model": "",
        }
    }
    assert is_luna_session(session_view) is True


def test_bound_conversation_is_the_explicit_runtime_luna_proof():
    snapshot = {
        "sessionId": "ai-dev-control-plane-42",
        "harness": "codex",
        "settings": {"model": "gpt-5.6-luna"},
        "messages": [],
    }
    assert probe.is_bound_luna_conversation(snapshot, "ai-dev-control-plane-42") is True


def test_conversation_model_gate_fails_closed():
    assert probe.is_bound_luna_conversation(
        {"sessionId": "ai-dev-control-plane-42", "harness": "codex", "settings": {}},
        "ai-dev-control-plane-42",
    ) is False
    assert probe.is_bound_luna_conversation(
        {"sessionId": "ai-dev-control-plane-42", "harness": "codex", "settings": {"model": "gpt-5.6-terra"}},
        "ai-dev-control-plane-42",
    ) is False
    assert probe.is_bound_luna_conversation(
        {"sessionId": "other", "harness": "codex", "settings": {"model": "gpt-5.6-luna"}},
        "ai-dev-control-plane-42",
    ) is False
    assert probe.is_bound_luna_conversation(
        {"sessionId": "ai-dev-control-plane-42", "harness": "claude-code", "settings": {"model": "gpt-5.6-luna"}},
        "ai-dev-control-plane-42",
    ) is False
