import scripts.aictrl_pre003_probe as probe


def test_conversation_binding_does_not_duplicate_session_model_gate():
    snapshot = {
        "sessionId": "ai-dev-control-plane-42",
        "harness": "codex",
        "settings": {},
        "messages": [],
    }
    assert probe.is_bound_luna_conversation(snapshot, "ai-dev-control-plane-42") is True


def test_conversation_binding_rejects_wrong_session_or_harness():
    assert probe.is_bound_luna_conversation(
        {"sessionId": "other", "harness": "codex", "settings": {"model": "gpt-5.6-luna"}},
        "ai-dev-control-plane-42",
    ) is False
    assert probe.is_bound_luna_conversation(
        {"sessionId": "ai-dev-control-plane-42", "harness": "claude-code", "settings": {"model": "gpt-5.6-luna"}},
        "ai-dev-control-plane-42",
    ) is False
