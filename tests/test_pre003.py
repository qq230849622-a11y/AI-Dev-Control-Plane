import json
import subprocess
import sys
from pathlib import Path

import scripts.aictrl_pre003_probe as probe_module

from aictrl.pre003 import (
    EXPECTED_ACTOR,
    EXPECTED_HEADER,
    EXPECTED_ISSUE_NUMBER,
    EXPECTED_MODEL,
    EXPECTED_PONG_AUTHOR,
    EXPECTED_PROJECT_KEY,
    EXPECTED_REPOSITORY,
    admit_issue_comment,
    build_probe_marker,
    has_matching_pong,
    has_exact_session_result,
    is_luna_session,
)
from scripts.aictrl_pre003_probe import is_isolated_worktree


def valid_event(**overrides):
    event = {
        "action": "created",
        "repository": {"full_name": EXPECTED_REPOSITORY},
        "sender": {"login": EXPECTED_ACTOR},
        "issue": {"number": EXPECTED_ISSUE_NUMBER},
        "comment": {
            "id": 123456789,
            "body": "\n".join(
                [
                    EXPECTED_HEADER,
                    f"project_key: {EXPECTED_PROJECT_KEY}",
                    f"repo: {EXPECTED_REPOSITORY}",
                    "event_id: controller-supplied-value-is-not-trusted",
                    f"model: {EXPECTED_MODEL}",
                ]
            ),
        },
    }
    event.update(overrides)
    return event


def test_admits_exact_pre003_ping_and_preserves_its_event_id():
    result = admit_issue_comment(valid_event())

    assert result.admitted is True
    assert result.event_id == "controller-supplied-value-is-not-trusted"
    assert result.reason is None


def test_rejects_comment_with_duplicate_probe_headers():
    event = valid_event()
    event["comment"]["body"] += f"\n{EXPECTED_HEADER}"

    result = admit_issue_comment(event)

    assert result.admitted is False
    assert result.reason == "INVALID_ENVELOPE"

    event = valid_event()
    event["comment"]["body"] = event["comment"]["body"].replace(
        "event_id: controller-supplied-value-is-not-trusted", "event_id: "
    )

    result = admit_issue_comment(event)

    assert result.admitted is False
    assert result.reason == "INVALID_ENVELOPE"


def test_rejects_any_mismatched_binding():
    for field, bad_value in [
        ("repository", {"full_name": "qq230849622-a11y/other"}),
        ("sender", {"login": "other-actor"}),
        ("issue", {"number": EXPECTED_ISSUE_NUMBER + 1}),
        ("action", "edited"),
    ]:
        result = admit_issue_comment(valid_event(**{field: bad_value}))

        assert result.admitted is False
        assert result.reason == "EVENT_BINDING_MISMATCH"


def test_rejects_malformed_event_bindings_without_raising():
    for field in ["repository", "sender", "issue", "comment"]:
        result = admit_issue_comment(valid_event(**{field: []}))

        assert result.admitted is False
        assert result.reason == "EVENT_BINDING_MISMATCH"


def test_rejects_comment_model_or_declared_event_id_mismatch():
    event = valid_event()
    event["comment"]["body"] = event["comment"]["body"].replace(
        f"model: {EXPECTED_MODEL}", "model: gpt-5.6-terra"
    )

    result = admit_issue_comment(event)

    assert result.admitted is False
    assert result.reason == "INVALID_ENVELOPE"


def test_detects_only_a_bound_pong_for_the_same_event_id():
    matching = "\n".join(
        [
            "AICTRL_PRE003_PONG_V1",
            f"project_key: {EXPECTED_PROJECT_KEY}",
            f"repo: {EXPECTED_REPOSITORY}",
            "event_id: event-42",
            "runner: AICTRL-WIN11",
            f"model: {EXPECTED_MODEL}",
            "session_id: session-1",
            f"result_marker: {build_probe_marker('event-42')}",
            "main_head: 0123456789abcdef0123456789abcdef01234567",
            "main_unchanged: true",
            "worktree_path: C:\\AO\\worktrees\\pre003-event-42",
            "worktree_isolated: true",
            "status: PASS",
        ]
    )
    owner_comment = {"body": matching, "user": {"login": EXPECTED_PONG_AUTHOR}}
    untrusted_comment = {"body": matching, "user": {"login": "untrusted"}}
    wrong_repo = {"body": matching.replace(EXPECTED_REPOSITORY, "qq230849622-a11y/other"), "user": {"login": EXPECTED_PONG_AUTHOR}}

    assert has_matching_pong([wrong_repo, untrusted_comment, owner_comment], "event-42") is True
    assert has_matching_pong([wrong_repo, untrusted_comment], "event-42") is False
    assert has_matching_pong([owner_comment], "event-43") is False


def test_accepts_only_an_exact_result_field_from_a_luna_session():
    marker = build_probe_marker("event-42")
    session_view = {"session": {"id": "session-1", "harness": "codex", "model": EXPECTED_MODEL}}
    conversation_snapshot = {
        "sessionId": "session-1",
        "harness": "codex",
        "settings": {"model": EXPECTED_MODEL},
        "messages": [{"role": "assistant", "origin": "provider", "text": marker}],
    }
    prompt_echo_only = {
        "sessionId": "session-1",
        "harness": "codex",
        "settings": {"model": EXPECTED_MODEL},
        "messages": [{"role": "user", "origin": "user", "text": marker}],
    }
    reduced_cli_dto = {"session": {"model": EXPECTED_MODEL, "result": marker}}
    wrong_model = {"session": {"model": "gpt-5.6-terra"}}

    assert is_luna_session(session_view) is True
    assert has_exact_session_result(conversation_snapshot, marker) is True
    assert has_exact_session_result(prompt_echo_only, marker) is False
    assert has_exact_session_result(reduced_cli_dto, marker) is False
    assert is_luna_session(wrong_model) is False


def test_pre003_runtime_gates_use_v01210_machine_surfaces():
    catalog = {
        "agentId": "codex",
        "selectionMode": "catalog",
        "models": [{"id": EXPECTED_MODEL, "label": "Luna", "isDefault": False}],
    }
    sessions = {
        "sessions": [
            {"id": "other-session", "projectId": "other-project", "displayName": "other"},
            {"id": "probe-session", "projectId": "ai-dev-control-plane", "displayName": "pre003-123456789012"},
        ]
    }
    catalog_gate = getattr(probe_module, "catalog_has_luna", lambda _: False)
    login_gate = getattr(probe_module, "is_chatgpt_login_status", lambda _: False)
    session_by_name = getattr(probe_module, "session_id_by_name", lambda *_: None)

    assert catalog_gate(catalog) is True
    assert catalog_gate({"models": [{"id": "gpt-5.6-terra"}]}) is False
    assert login_gate("Logged in using ChatGPT\n") is True
    assert login_gate("Logged in using an API key\n") is False
    assert session_by_name(sessions, "pre003-123456789012") == "probe-session"



def test_admission_cli_writes_github_actions_outputs(tmp_path):
    event_path = tmp_path / "event.json"
    comments_path = tmp_path / "comments.json"
    output_path = tmp_path / "github-output.txt"
    event_path.write_text(json.dumps(valid_event()), encoding="utf-8")
    comments_path.write_text(json.dumps([[{"body": "unrelated comment", "user": {"login": "untrusted"}}]]), encoding="utf-8")
    project_root = Path(__file__).parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/aictrl_pre003_admit.py",
            "--event",
            str(event_path),
            "--comments",
            str(comments_path),
            "--output",
            str(output_path),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "ADMITTED"
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "admitted=true",
        "event_id=controller-supplied-value-is-not-trusted",
        "duplicate=false",
        "reason=",
    ]


def test_probe_marker_is_deterministic_and_event_bound():
    assert build_probe_marker("event-42") == build_probe_marker("event-42")
    assert build_probe_marker("event-42") != build_probe_marker("event-43")
    assert build_probe_marker("event-42").startswith("AICTRL_PRE003_MARKER_")


def test_worktree_guard_rejects_an_unrelated_git_repository(tmp_path):
    main = tmp_path / "main"
    unrelated = tmp_path / "unrelated"
    for path in (main, unrelated):
        path.mkdir()
        result = subprocess.run(
            ["git", "init"], cwd=path, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0

    assert is_isolated_worktree(unrelated, main) is False


def test_probe_cli_fails_closed_when_ao_is_unavailable(tmp_path):
    output_path = tmp_path / "github-output.txt"
    project_root = Path(__file__).parents[1]
    environment = {"AO_BIN": str(tmp_path / "missing-ao.exe")}

    result = subprocess.run(
        [
            sys.executable,
            "scripts/aictrl_pre003_probe.py",
            "--event-id",
            "event-42",
            "--output",
            str(output_path),
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "FAIL"
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "status=FAIL",
        "reason=AO_BINARY_UNAVAILABLE",
        "session_id=",
        "result_marker=AICTRL_PRE003_MARKER_96f205e753ea39f1",
        "main_head=",
        "worktree_path=",
    ]


def test_pre003_workflow_is_strictly_bound_and_has_no_pr_trigger():
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "aictrl-pre003.yml"
    ).read_text(encoding="utf-8")

    assert "issue_comment:" in workflow
    assert "types: [created]" in workflow
    assert "runs-on: [self-hosted, Windows, X64, aictrl-win]" in workflow
    assert "contents: read" in workflow
    assert "issues: write" in workflow
    assert "timeout-minutes: 15" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "github.repository == 'qq230849622-a11y/AI-Dev-Control-Plane'" in workflow
    assert "github.actor == 'qq230849622-a11y'" in workflow
    assert "github.event.issue.number == 7" in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "gpt-5.6-luna" in workflow
    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in workflow
    assert "always()" in workflow
    assert "PROBE_RUNTIME_ERROR" in workflow
    assert "worktree_path: $env:PRE003_WORKTREE_PATH" in workflow
    assert "pull_request:" not in workflow
    assert "pull_request_target:" not in workflow
