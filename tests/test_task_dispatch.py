import re
import json
import os
from pathlib import Path
import subprocess
import sys
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


def test_rename_aware_scope_verification_rejects_forbidden_source(monkeypatch):
    task = task_document()
    task["allowed_scope"] = ["src/aictrl/**"]
    task["forbidden_scope"] = ["schemas/v1/**"]
    calls = []
    monkeypatch.setattr(dispatch, "git", lambda _, *args, **__: calls.append(args) or "schemas/v1/task.schema.json\nsrc/aictrl/task.py\n")
    paths = dispatch.worker_changed_paths("workspace", SimpleNamespace(default_branch="master"))
    assert "--no-renames" in calls[0]
    with pytest.raises(dispatch.DispatchFailure, match="WORKER_FORBIDDEN_SCOPE"):
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


def test_ao_project_summary_is_resolved_through_project_get_before_binding_match(monkeypatch):
    binding = SimpleNamespace(repo="qq230849622-a11y/AI-Dev-Control-Plane", default_branch="master")
    responses = [
        {"projects": [{"id": "other"}, {"id": "expected"}]},
        {"project": {"id": "other", "repo": "https://github.com/example/other.git", "defaultBranch": "main"}},
        {"project": {"id": "expected", "repo": "https://github.com/qq230849622-a11y/AI-Dev-Control-Plane.git", "config": {"defaultBranch": "master"}}},
    ]
    calls = []
    monkeypatch.setattr(dispatch, "command_json", lambda *args: calls.append(args) or responses.pop(0))
    project = dispatch.find_ao_project("ao.exe", binding)
    assert project["id"] == "expected"
    assert calls[1][1:4] == ("project", "get", "other")


def test_worker_brief_references_validated_issue_and_is_bounded():
    brief = dispatch.worker_brief(task_document(), "aictrl/task", "master", 29)
    assert f"issues/29" in brief
    assert dispatch.TASK_BEGIN in brief and json.dumps(task_document(), sort_keys=True) not in brief
    assert len(brief) <= dispatch.MAX_WORKER_BRIEF_LENGTH
    with pytest.raises(dispatch.DispatchFailure, match="CONTROLLER_ISSUE_CONTEXT_UNAVAILABLE"):
        dispatch.worker_brief(task_document(), "aictrl/task", "master", "29")


def test_worker_brief_send_fails_closed_at_ao_message_limit(monkeypatch):
    monkeypatch.setattr(dispatch, "command_success", lambda *_: pytest.fail("must not send an oversized brief"))
    with pytest.raises(dispatch.DispatchFailure, match="WORKER_BRIEF_TOO_LARGE"):
        dispatch.send_worker_brief("ao.exe", "session-1", "x" * 4097)


def test_desktop_thread_name_is_deterministic_human_readable_and_bounded():
    assert dispatch.desktop_thread_name("PRE-005", "gpt-5.6-terra") == "AICTRL PRE-005 terra"
    long_task_id = "PRE-" + "x" * 200
    name = dispatch.desktop_thread_name(long_task_id, "gpt-5.6-luna")
    assert name.startswith("AICTRL PRE-") and name.endswith(" luna")
    assert len(name) <= dispatch.MAX_DESKTOP_THREAD_NAME
    assert name == dispatch.desktop_thread_name(long_task_id, "gpt-5.6-luna")


def test_desktop_thread_title_round_trip_and_provider_id_are_verified_before_brief(monkeypatch):
    commands = []
    monkeypatch.setattr(dispatch, "command_success", lambda *args: commands.append(args) or True)
    monkeypatch.setattr(dispatch, "session_snapshot", lambda *_: {"displayName": "AICTRL PRE-005 terra"})
    monkeypatch.setattr(dispatch, "conversation_snapshot", lambda *_: {"providerThreadId": "codex-thread-1"})
    assert dispatch.set_and_verify_desktop_thread("ao.exe", {"port": 1}, "session-1", "project-1", "AICTRL PRE-005 terra") == "codex-thread-1"
    assert commands == [("ao.exe", "session", "rename", "session-1", "AICTRL PRE-005 terra", "--project", "project-1")]


@pytest.mark.parametrize("session, snapshot, code", [
    (None, {"providerThreadId": "codex-thread-1"}, "DESKTOP_THREAD_TITLE_UNVERIFIED"),
    ({"displayName": "other"}, {"providerThreadId": "codex-thread-1"}, "DESKTOP_THREAD_TITLE_UNVERIFIED"),
    ({"displayName": "AICTRL PRE-005 terra"}, {}, "PROVIDER_THREAD_ID_UNAVAILABLE"),
])
def test_desktop_metadata_failures_close_before_worker_brief(monkeypatch, session, snapshot, code):
    monkeypatch.setattr(dispatch, "command_success", lambda *_: True)
    monkeypatch.setattr(dispatch, "session_snapshot", lambda *_: session)
    monkeypatch.setattr(dispatch, "conversation_snapshot", lambda *_: snapshot)
    with pytest.raises(dispatch.DispatchFailure, match=code):
        dispatch.set_and_verify_desktop_thread("ao.exe", {"port": 1}, "session-1", "project-1", "AICTRL PRE-005 terra")


def test_desktop_thread_title_set_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(dispatch, "command_success", lambda *_: False)
    with pytest.raises(dispatch.DispatchFailure, match="DESKTOP_THREAD_TITLE_SET_FAILED"):
        dispatch.set_and_verify_desktop_thread("ao.exe", {"port": 1}, "session-1", "project-1", "AICTRL PRE-005 terra")


def test_post_worker_desktop_metadata_is_reverified_before_review_evidence(monkeypatch):
    monkeypatch.setattr(dispatch, "session_snapshot", lambda *_: {"displayName": "AICTRL PRE-005 terra"})
    monkeypatch.setattr(dispatch, "conversation_snapshot", lambda *_: {"providerThreadId": "codex-thread-1"})
    assert dispatch.reverify_desktop_thread("ao.exe", {"port": 1}, "session-1", "project-1", "AICTRL PRE-005 terra", "codex-thread-1") is None


@pytest.mark.parametrize("session, snapshot, code", [
    (None, {"providerThreadId": "codex-thread-1"}, "DESKTOP_THREAD_TITLE_UNVERIFIED"),
    ({"displayName": "other"}, {"providerThreadId": "codex-thread-1"}, "DESKTOP_THREAD_TITLE_UNVERIFIED"),
    ({"displayName": "AICTRL PRE-005 terra"}, {}, "PROVIDER_THREAD_ID_UNAVAILABLE"),
    ({"displayName": "AICTRL PRE-005 terra"}, {"providerThreadId": "other-thread"}, "PROVIDER_THREAD_ID_UNVERIFIED"),
])
def test_post_worker_desktop_metadata_absence_or_drift_fails_closed(monkeypatch, session, snapshot, code):
    monkeypatch.setattr(dispatch, "session_snapshot", lambda *_: session)
    monkeypatch.setattr(dispatch, "conversation_snapshot", lambda *_: snapshot)
    with pytest.raises(dispatch.DispatchFailure, match=code):
        dispatch.reverify_desktop_thread("ao.exe", {"port": 1}, "session-1", "project-1", "AICTRL PRE-005 terra", "codex-thread-1")


def test_direct_script_entry_bootstraps_repo_src_without_pythonpath(tmp_path):
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    script = Path(__file__).parents[1] / "scripts" / "aictrl_task_dispatch.py"
    result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "--event" in result.stdout and "--result" in result.stdout


def test_target_repository_gh_calls_clear_actions_tokens(monkeypatch):
    calls = []
    monkeypatch.setenv("GH_TOKEN", "controller-token")
    monkeypatch.setenv("GITHUB_TOKEN", "controller-token")
    monkeypatch.setattr(dispatch, "run", lambda command, **kwargs: calls.append((command, kwargs.get("env"))) or SimpleNamespace(returncode=0, stdout="[]"))
    dispatch.github_json(["issue", "view", "20"], "ISSUE_LOOKUP_FAILED")
    dispatch.github_json(["pr", "list"], "PR_LOOKUP_FAILED", target_repository=True)
    assert calls[0][1] is None
    assert "GH_TOKEN" not in calls[1][1] and "GITHUB_TOKEN" not in calls[1][1]


def test_worker_spawn_is_chat_mode_with_model_but_without_an_initial_task_turn(monkeypatch):
    commands = []
    monkeypatch.setattr(dispatch, "run", lambda command, **_: commands.append(command) or SimpleNamespace(returncode=0, stdout="spawned session session-1 (chat)"))
    assert dispatch.spawn_worker("ao.exe", "project-1", 20, "gpt-5.6-terra", "aictrl/task", "task") == "session-1"
    assert "--prompt" not in commands[0]
    assert commands[0][commands[0].index("--model") + 1] == "gpt-5.6-terra"
    assert commands[0][commands[0].index("--mode") + 1] == "chat"


def test_worker_session_name_is_deterministic_and_limited_to_twenty_characters():
    assert dispatch.worker_session_name("CTRL-PROD-001") == "aictrl-ctrl-prod-001"
    long_task_id = "CTRL-" + "X" * 80
    name = dispatch.worker_session_name(long_task_id)
    assert len(name) <= 20
    assert name == dispatch.worker_session_name(long_task_id)
    assert name != dispatch.worker_session_name("CTRL-" + "Y" * 80)


def test_worker_brief_leaves_canonical_testing_policy_to_controller():
    brief = dispatch.worker_brief(task_document(), "aictrl/task", "master", 29)
    assert "Run the task testing_policy commands" not in brief
    assert "controller owns the canonical testing_policy" in brief


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


def test_unexpected_post_spawn_failure_still_runs_cleanup_and_cleanup_overrides(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "cleanup_session_confirmed", lambda *args: calls.append(args) or True)
    result_path = tmp_path / "result.txt"
    assert dispatch.finish_execution(result_path, "UNEXPECTED_RUNTIME_ERROR", "", "ao.exe", {"port": 1}, "session-1", "project-1") == 1
    assert calls == [("ao.exe", {"port": 1}, "session-1", "project-1")]
    assert "UNEXPECTED_RUNTIME_ERROR" in result_path.read_text(encoding="utf-8")
    monkeypatch.setattr(dispatch, "cleanup_session_confirmed", lambda *args: False)
    assert dispatch.finish_execution(result_path, "UNEXPECTED_RUNTIME_ERROR", "", "ao.exe", {"port": 1}, "session-1", "project-1") == 1
    assert "SESSION_CLEANUP_FAILED" in result_path.read_text(encoding="utf-8")


def test_review_event_is_schema_valid_and_carries_controller_evidence():
    task = task_document()
    event = dispatch.review_event(task, "event-2", {"number": 8, "url": "https://example.test/pr/8"}, "a" * 40, "gpt-5.6-terra", "medium", "session-1", "AICTRL PRE-005 terra", "codex-thread-1")
    assert dispatch.validate_document(event).valid
    assert event["payload"]["session_id"] == "session-1"
    assert event["payload"]["desktop_thread_name"] == "AICTRL PRE-005 terra"
    assert event["payload"]["provider_thread_id"] == "codex-thread-1"
    with pytest.raises(dispatch.DispatchFailure, match="REVIEW_THREAD_EVIDENCE_INVALID"):
        dispatch.review_event(task, "event-2", {"number": 8, "url": "https://example.test/pr/8"}, "a" * 40, "gpt-5.6-terra", "medium", "session-1", "", "")


def test_generic_workflow_is_issue_comment_only_and_initializes_evidence_first():
    workflow = (Path(__file__).parents[1] / ".github/workflows/aictrl-task-dispatch.yml").read_text(encoding="utf-8")
    assert "issue_comment:" in workflow and "types: [created]" in workflow
    assert "pull_request:" not in workflow and "pull_request_target:" not in workflow
    assert "github.event.issue.pull_request == null" in workflow
    assert "runs-on: [self-hosted, Windows, X64, aictrl-win]" in workflow
    assert re.search(r"actions/checkout@[0-9a-f]{40}", workflow)
    assert workflow.count("GH_TOKEN: ${{ github.token }}") == 2
    assert "group: aictrl-dispatch-ai-dev-control-plane" in workflow
    assert "github.event.comment.id" not in workflow
    assert "permissions:\n  contents: read\n  issues: write" in workflow
    assert workflow.index("Initialize bounded failure evidence") < workflow.index("Check out trusted default branch")
    assert "aictrl_task_dispatch_bootstrap.py" not in workflow
    assert "python scripts/aictrl_task_dispatch.py" in workflow
