import json
from pathlib import Path

import pytest

import scripts.aictrl_pre004_dispatch as pre004


def valid_event():
    return {
        "action": "created",
        "repository": {"full_name": pre004.REPOSITORY},
        "sender": {"login": "qq230849622-a11y"},
        "issue": {"number": pre004.ISSUE_NUMBER},
        "comment": {"body": pre004.EXPECTED_BODY},
    }


def test_pre004_admits_only_exact_bound_start(tmp_path):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(valid_event()), encoding="utf-8")
    pre004.admit_event(path)

    for mutation in (
        lambda event: event["repository"].update(full_name="qq230849622-a11y/other"),
        lambda event: event["sender"].update(login="other"),
        lambda event: event["issue"].update(number=16),
        lambda event: event["comment"].update(body=pre004.EXPECTED_BODY + "\nextra"),
        lambda event: event["comment"].update(
            body=pre004.EXPECTED_BODY.replace(pre004.MODEL, "gpt-5.6-sol")
        ),
    ):
        event = valid_event()
        mutation(event)
        path.write_text(json.dumps(event), encoding="utf-8")
        with pytest.raises(pre004.ProbeFailure, match="EVENT_BINDING_MISMATCH"):
            pre004.admit_event(path)


def test_pre004_runtime_model_gate_is_terra_only():
    assert pre004.catalog_has_terra(
        {
            "agentId": "codex",
            "models": [{"id": "gpt-5.6-terra"}, {"id": "gpt-5.6-sol"}],
        }
    ) is True
    assert pre004.catalog_has_terra(
        {"agentId": "codex", "models": [{"id": "gpt-5.6-sol"}]}
    ) is False
    assert pre004.bound_terra_conversation(
        {
            "sessionId": "ai-dev-control-plane-9",
            "harness": "codex",
            "settings": {"model": "gpt-5.6-terra"},
        },
        "ai-dev-control-plane-9",
    ) is True
    assert pre004.bound_terra_conversation(
        {
            "sessionId": "ai-dev-control-plane-9",
            "harness": "codex",
            "settings": {"model": "gpt-5.6-sol"},
        },
        "ai-dev-control-plane-9",
    ) is False


def test_pre004_ready_marker_requires_exact_provider_assistant_message():
    assert pre004.has_ready_marker(
        {
            "messages": [
                {
                    "role": "assistant",
                    "origin": "provider",
                    "text": pre004.READY_MARKER,
                }
            ]
        }
    ) is True
    assert pre004.has_ready_marker(
        {"messages": [{"role": "user", "origin": "human", "text": pre004.READY_MARKER}]}
    ) is False
    assert pre004.has_ready_marker(
        {
            "messages": [
                {
                    "role": "assistant",
                    "origin": "provider",
                    "text": pre004.READY_MARKER + " extra",
                }
            ]
        }
    ) is False


def test_pre004_workflow_is_strictly_bound_to_owner_issue_and_windows_runner():
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "aictrl-pre004.yml"
    ).read_text(encoding="utf-8")

    assert "issue_comment:" in workflow
    assert "types: [created]" in workflow
    assert "github.repository == 'qq230849622-a11y/AI-Dev-Control-Plane'" in workflow
    assert "github.actor == 'qq230849622-a11y'" in workflow
    assert "github.event.issue.number == 15" in workflow
    assert "startsWith(github.event.comment.body, 'AICTRL_PRE004_START_V1')" in workflow
    assert "runs-on: [self-hosted, Windows, X64, aictrl-win]" in workflow
    assert "timeout-minutes: 20" in workflow
    assert "contents: read" in workflow
    assert "issues: write" in workflow
    assert "pull-requests: read" in workflow
    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in workflow
    assert "shell: powershell" in workflow
    assert "if: always()" in workflow
    assert "DISPATCHER_NOT_COMPLETED" in workflow
    assert "pull_request:" not in workflow
    assert "pull_request_target:" not in workflow
