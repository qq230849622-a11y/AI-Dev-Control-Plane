import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.aictrl_task_dispatch as dispatch
from test_validator import result_document, task_document


def valid_event(body=None):
    return {
        "action": "created",
        "repository": {"full_name": dispatch.CONTROLLER_REPOSITORY},
        "sender": {"login": dispatch.CONTROLLER_OWNER},
        "issue": {"number": 20},
        "comment": {"id": 9, "user": {"login": dispatch.CONTROLLER_OWNER}, "body": body or "\n".join([
            dispatch.DISPATCH_HEADER,
            "project_key: AI_DEV_CONTROL_PLANE",
            "repo: qq230849622-a11y/AI-Dev-Control-Plane",
            "task_id: CTRL-PROD-001",
            "head_sha: e2663570e48dcdaf85ec7a9e9d7ed703e14c88cc",
            "event_id: event-20",
        ])},
    }


def test_admission_requires_new_owner_issue_comment_with_exact_trigger():
    fields, number, comment_id = dispatch.admit_event(valid_event())
    assert fields["event_id"] == "event-20" and (number, comment_id) == (20, 9)
    for mutation in (
        lambda event: event.update(action="edited"),
        lambda event: event["sender"].update(login="other"),
        lambda event: event["comment"]["user"].update(login="other"),
        lambda event: event["issue"].update(pull_request={}),
        lambda event: event["comment"].update(body=event["comment"]["body"] + "\nextra"),
    ):
        event = valid_event()
        mutation(event)
        with pytest.raises(dispatch.DispatchFailure):
            dispatch.admit_event(event)


def test_task_envelope_requires_one_delimited_valid_task():
    task = task_document()
    body = f"before\n{dispatch.TASK_BEGIN}\n{json.dumps(task)}\n{dispatch.TASK_END}\nafter"
    assert dispatch.task_from_issue_body(body)["task_id"] == "CTRL-002"
    with pytest.raises(dispatch.DispatchFailure, match="TASK_ENVELOPE_INVALID"):
        dispatch.task_from_issue_body(body + f"\n{dispatch.TASK_BEGIN}\n{{}}\n{dispatch.TASK_END}")


def test_issue_author_must_be_the_controller_owner():
    dispatch.require_controller_issue_author({"author": {"login": dispatch.CONTROLLER_OWNER}})
    with pytest.raises(dispatch.DispatchFailure, match="ISSUE_AUTHOR_MISMATCH"):
        dispatch.require_controller_issue_author({"author": {"login": "other"}})


@pytest.mark.parametrize("model, complexity, accepted", [
    ("luna", "C0", True), ("luna", "C2", False), ("terra", "C3", True),
    ("terra", "C0", False), ("sol", "C3", False), ("terra", "C4", False),
])
def test_model_policy_has_no_sol_or_silent_fallback(model, complexity, accepted):
    task = task_document()
    task.update(model=model, complexity=complexity, max_attempts=1, owner_gate_required=False)
    fields = {key: task[key] for key in ("project_key", "repo", "task_id", "head_sha")}
    fields["event_id"] = "e"
    if accepted:
        dispatch.validate_task_policy(task, fields)
    else:
        with pytest.raises(dispatch.DispatchFailure, match="MODEL_POLICY_REJECTED"):
            dispatch.validate_task_policy(task, fields)


def test_task_guard_rejects_goal_owner_gate_status_and_stale_binding():
    task = task_document()
    task.update(max_attempts=1, owner_gate_required=False)
    fields = {key: task[key] for key in ("project_key", "repo", "task_id", "head_sha")}
    fields["event_id"] = "e"
    for key, value in (("goal_mode", True), ("owner_gate_required", True), ("status", "BLOCKED")):
        invalid = dict(task)
        invalid[key] = value
        with pytest.raises(dispatch.DispatchFailure):
            dispatch.validate_task_policy(invalid, fields)
    fields["head_sha"] = "0" * 40
    with pytest.raises(dispatch.DispatchFailure, match="DISPATCH_TASK_BINDING_MISMATCH"):
        dispatch.validate_task_policy(task, fields)


def test_scope_verification_requires_allowed_and_not_forbidden_paths():
    task = task_document()
    task["allowed_scope"] = ["src/aictrl/**", "tests/**"]
    task["forbidden_scope"] = ["schemas/v1/**", "scripts/aictrl_pre004*"]
    dispatch.verify_scope(["src/aictrl/dispatch.py", "tests/test_dispatch.py"], task)
    for paths in (["schemas/v1/task.schema.json"], ["README.md"], ["scripts/aictrl_pre004_dispatch.py"]):
        with pytest.raises(dispatch.DispatchFailure):
            dispatch.verify_scope(paths, task)


def test_final_provider_result_is_delimited_valid_and_bound_to_final_message():
    result = result_document()
    text = f"{dispatch.RESULT_BEGIN}\n{json.dumps(result)}\n{dispatch.RESULT_END}"
    snapshot = {"messages": [
        {"role": "assistant", "origin": "provider", "text": "working"},
        {"role": "assistant", "origin": "provider", "text": text},
    ]}
    assert dispatch.final_provider_result(snapshot)["status"] == "READY_FOR_REVIEW"
    snapshot["messages"][-1]["text"] = text + "\nextra"
    with pytest.raises(dispatch.DispatchFailure, match="WORKER_RESULT_INVALID"):
        dispatch.final_provider_result(snapshot)


def test_conversation_settings_bind_model_and_reasoning_before_brief(monkeypatch):
    snapshot = {"sessionId": "s1", "harness": "codex", "settings": {"model": "gpt-5.6-terra", "reasoningEffort": "medium"}}
    calls = []
    def fake_api(_, path, **kwargs):
        calls.append((path, kwargs))
        return {} if kwargs else snapshot
    monkeypatch.setattr(dispatch, "api_document", fake_api)
    dispatch.set_conversation_settings({"port": 1}, "s1", "gpt-5.6-terra", "medium")
    assert calls[0][1] == {"method": "PATCH", "payload": {"model": "gpt-5.6-terra", "reasoningEffort": "medium"}}


def test_worker_spawn_is_chat_mode_with_model_but_without_an_initial_task_turn(monkeypatch):
    commands = []
    monkeypatch.setattr(dispatch, "run", lambda command, **_: commands.append(command) or SimpleNamespace(returncode=0, stdout="spawned session session-1 (chat)"))
    assert dispatch.spawn_worker("ao.exe", "project-1", 20, "gpt-5.6-terra", "aictrl/task", "task") == "session-1"
    assert "--prompt" not in commands[0]
    assert commands[0][commands[0].index("--model") + 1] == "gpt-5.6-terra"
    assert commands[0][commands[0].index("--mode") + 1] == "chat"


def test_pr_metadata_requires_final_worker_head_and_no_auto_merge():
    pr = {
        "number": 8, "url": "https://example.test/pr/8", "state": "OPEN", "isDraft": False,
        "headRefName": "aictrl/task", "baseRefName": "master", "headRefOid": "a" * 40,
        "autoMergeRequest": None,
    }
    dispatch.verify_pr_metadata(pr, "aictrl/task", "master", "a" * 40)
    for key, value in (("headRefOid", "b" * 40), ("autoMergeRequest", {"enabledAt": "now"})):
        invalid = dict(pr)
        invalid[key] = value
        with pytest.raises(dispatch.DispatchFailure, match="PR_STATE_MISMATCH"):
            dispatch.verify_pr_metadata(invalid, "aictrl/task", "master", "a" * 40)


def test_session_cleanup_fails_closed_on_unexpected_error(monkeypatch):
    monkeypatch.setattr(dispatch, "command_success", lambda *_: (_ for _ in ()).throw(RuntimeError("broken")))
    assert dispatch.cleanup_session_confirmed("ao.exe", {"port": 1}, "session-1", "project-1") is False


def test_review_event_is_schema_valid_and_carries_controller_evidence():
    task = task_document()
    event = dispatch.review_event(task, "event-2", {"number": 8, "url": "https://example.test/pr/8"}, "a" * 40, "gpt-5.6-terra", "medium", "session-1")
    assert dispatch.validate_document(event).valid
    assert event["payload"]["session_id"] == "session-1"


def test_generic_workflow_is_issue_comment_only_and_initializes_evidence_first():
    workflow = (Path(__file__).parents[1] / ".github/workflows/aictrl-task-dispatch.yml").read_text(encoding="utf-8")
    assert "issue_comment:" in workflow and "types: [created]" in workflow
    assert "pull_request:" not in workflow and "pull_request_target:" not in workflow
    assert "github.event.issue.pull_request == null" in workflow
    assert "runs-on: [self-hosted, Windows, X64, aictrl-win]" in workflow
    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in workflow
    assert "GH_TOKEN:" not in workflow
    assert "permissions:\n  contents: read" in workflow
    assert workflow.index("Initialize bounded failure evidence") < workflow.index("Check out trusted default branch")
