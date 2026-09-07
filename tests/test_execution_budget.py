import json
import subprocess
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path

import pytest

from aictrl.execution_budget import GateEvidence, budget_for, input_fingerprint
from aictrl.validator import validate_document
import scripts.aictrl_task_dispatch as dispatch
from test_validator import task_document


def goal_task():
    task = task_document()
    task.update(max_attempts=1, owner_gate_required=False)
    task["execution_budget_policy"] = {
        "version": 1, "worker_seconds": 3600, "self_repair_rounds": 3,
        "reuse_seconds": 300, "reusable_commands": ["check"],
    }
    task["testing_policy"] = {"required": True, "commands": ["check", "check"]}
    return task


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("worker_seconds", 3601), ("worker_seconds", True),
    ("self_repair_rounds", 0), ("reuse_seconds", -1), ("unsafe_override", True),
])
def test_schema_rejects_invalid_budget(field, value):
    task = goal_task()
    task["execution_budget_policy"][field] = value
    assert not validate_document(task).valid


def test_legacy_and_goal_contracts_and_safety_gates():
    assert validate_document(task_document()).valid
    task = goal_task()
    assert validate_document(task).valid
    fields = {k: task[k] for k in ("project_key", "repo", "task_id", "head_sha")}
    dispatch.validate_task_policy(task, fields)
    for field, value in [("goal_mode", True), ("max_attempts", 2),
                         ("owner_gate_required", True), ("repo", "other/repo")]:
        invalid = deepcopy(task)
        invalid[field] = value
        with pytest.raises(dispatch.DispatchFailure):
            dispatch.validate_task_policy(invalid, fields)
    task["execution_budget_policy"]["reusable_commands"] = ["undeclared"]
    with pytest.raises(dispatch.DispatchFailure, match="EXECUTION_BUDGET_COMMAND_MISMATCH"):
        dispatch.validate_task_policy(task, fields)
    assert budget_for(task_document())["worker_seconds"] == 1200


def test_brief_batches_goal_without_changing_provider_authority():
    brief = dispatch.worker_brief(goal_task(), "aictrl/goal", "master", 29)
    assert len(brief) <= dispatch.MAX_WORKER_BRIEF_LENGTH
    assert "3600s" in brief and "3 focused rounds" in brief
    assert "one complete outcome" in brief and "never merge" in brief
    assert "Do not start another agent, change model, use Goal mode" in brief


def test_evidence_expiry_disabled_reuse_and_changed_inputs():
    cache = GateEvidence(300)
    assert not cache.reusable("check", "a", 10)
    cache.record_success("check", "a", "a", 10)
    assert cache.reusable("check", "a", 310)
    for command, fingerprint, now in [("other", "a", 11), ("check", "b", 11),
                                      ("check", "a", 311), ("check", "a", 9)]:
        assert not cache.reusable(command, fingerprint, now)
    cache.record_success("changed", "a", "b", 10)
    assert not cache.reusable("changed", "b", 11)
    disabled = GateEvidence(0)
    disabled.record_success("check", "a", "a", 10)
    assert not disabled.reusable("check", "a", 10)


def test_fingerprint_binds_task_head_environment_and_ignored_files(tmp_path, monkeypatch):
    task = goal_task()
    (tmp_path / ".gitignore").write_text("dependency\n")
    dep = tmp_path / "dependency"
    dep.write_text("v1")
    before = input_fingerprint(tmp_path, task, "a" * 40)
    assert before == input_fingerprint(tmp_path, task, "a" * 40)
    assert before != input_fingerprint(tmp_path, task, "b" * 40)
    changed = deepcopy(task)
    changed["repo"] = "other/repo"
    assert before != input_fingerprint(tmp_path, changed, "a" * 40)
    dep.write_text("v2")
    assert before != input_fingerprint(tmp_path, task, "a" * 40)
    dep.write_text("v1")
    monkeypatch.setenv("AICTRL_TEST_ENVIRONMENT", "changed")
    assert before != input_fingerprint(tmp_path, task, "a" * 40)


def test_budget_gate_reuses_only_own_unchanged_success(tmp_path, monkeypatch):
    task = goal_task()
    monkeypatch.setattr(dispatch, "git", lambda *a: "a" * 40)
    calls = []
    monkeypatch.setattr(dispatch, "run", lambda c, **kw: calls.append(c) or SimpleNamespace(returncode=0))
    records = dispatch.run_budgeted_testing_policy(tmp_path, task)
    assert calls == ["check"]
    assert [r["status"] for r in records] == ["PASSED", "REUSED"]
    # A new invocation cannot import either worker evidence or previous success.
    dispatch.run_budgeted_testing_policy(tmp_path, task)
    assert calls == ["check", "check"]


def test_changed_input_and_nonhermetic_commands_run_again(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "git", lambda *a: "a" * 40)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        (tmp_path / "generated").write_text(str(len(calls)))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(dispatch, "run", run)
    assert [r["status"] for r in dispatch.run_budgeted_testing_policy(tmp_path, goal_task())] == ["PASSED", "PASSED"]
    task = goal_task()
    task["execution_budget_policy"]["reusable_commands"] = []
    dispatch.run_budgeted_testing_policy(tmp_path, task)
    assert len(calls) == 4


def test_failure_is_never_reused_or_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "git", lambda *a: "a" * 40)
    calls = []
    monkeypatch.setattr(dispatch, "run", lambda c, **kw: calls.append(c) or SimpleNamespace(returncode=1))
    with pytest.raises(dispatch.DispatchFailure, match="CONTROLLER_TESTS_FAILED"):
        dispatch.run_budgeted_testing_policy(tmp_path, goal_task())
    assert calls == ["check"]


def test_optional_gate_and_total_deadline(tmp_path, monkeypatch):
    task = goal_task()
    task["testing_policy"] = {"required": False, "commands": []}
    assert dispatch.run_budgeted_testing_policy(tmp_path, task) == []
    task["testing_policy"] = {"required": True, "commands": ["check"]}
    times = iter([0, 901])
    monkeypatch.setattr(dispatch.time, "monotonic", lambda: next(times))
    with pytest.raises(dispatch.DispatchFailure, match="CONTROLLER_TEST_BUDGET_EXHAUSTED"):
        dispatch.run_budgeted_testing_policy(tmp_path, task)


def test_worker_deadline_uses_elapsed_time(tmp_path, monkeypatch):
    times = iter([0, 3601])
    monkeypatch.setattr(dispatch.time, "monotonic", lambda: next(times))
    with pytest.raises(dispatch.DispatchFailure, match="WORKER_TIMEOUT"):
        dispatch.wait_for_worker({}, "session", goal_task(), "model", "medium")


def test_real_local_command_gate_emits_reuse(tmp_path):
    subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
                    "commit", "--allow-empty", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True)
    task = goal_task()
    task["testing_policy"]["commands"] = ["git rev-parse HEAD"] * 2
    task["execution_budget_policy"]["reusable_commands"] = ["git rev-parse HEAD"]
    records = dispatch.run_budgeted_testing_policy(tmp_path, task)
    assert [r["status"] for r in records] == ["PASSED", "REUSED"]
    assert len(records[0]["input_sha256"]) == 64


def test_input_type_change_and_expired_scan_disable_reuse(tmp_path):
    path = tmp_path / "input"
    path.mkdir()
    before = input_fingerprint(tmp_path, goal_task(), "a" * 40)
    path.rmdir()
    path.write_text("")
    assert before != input_fingerprint(tmp_path, goal_task(), "a" * 40)
    assert input_fingerprint(tmp_path, goal_task(), "a" * 40, deadline=0) is None


@pytest.mark.parametrize("drift,cleanup_ok,expected", [
    (False, True, None), (True, True, "POST_TEST_PR_CHANGED"),
    (False, False, "SESSION_CLEANUP_FAILED"),
])
def test_production_execute_budget_evidence_and_final_gates(tmp_path, monkeypatch, drift, cleanup_ok, expected):
    task = goal_task()
    fields = {k: task[k] for k in ("project_key", "repo", "task_id", "head_sha")}
    fields["event_id"] = "event-goal"
    workspace = tmp_path / "worker"
    workspace.mkdir()
    monkeypatch.setattr(dispatch, "read_event", lambda *a: {})
    monkeypatch.setattr(dispatch, "admit_event", lambda *a: (fields, 29, 1))
    issue = {"author": {"login": dispatch.CONTROLLER_OWNER}, "body":
             dispatch.TASK_BEGIN + "\n" + json.dumps(task) + "\n" + dispatch.TASK_END}
    monkeypatch.setattr(dispatch, "github_json", lambda args, *a, **kw: issue if args[0] == "issue" else [])
    monkeypatch.setattr(dispatch, "matching_dispatch_comments", lambda *a: [{"id": 1}])
    monkeypatch.setattr(dispatch, "route_task", lambda *a: SimpleNamespace(default_branch="master"))
    monkeypatch.setattr(dispatch, "ao_binary", lambda: Path(__file__))
    monkeypatch.setattr(dispatch, "ensure_ao_ready", lambda *a: {})
    monkeypatch.setattr(dispatch, "find_ao_project", lambda *a: {"id": "project"})
    monkeypatch.setattr(dispatch, "safe_sync_main", lambda *a: tmp_path)
    monkeypatch.setattr(dispatch, "has_chatgpt_login", lambda: True)
    monkeypatch.setattr(dispatch, "api_document", lambda *a: {"models": [{"id": "gpt-5.6-terra"}]})
    monkeypatch.setattr(dispatch, "spawn_worker", lambda *a: "session")
    monkeypatch.setattr(dispatch, "workspace_path", lambda *a: workspace)
    monkeypatch.setattr(dispatch, "set_and_verify_desktop_thread", lambda *a: "conversation")
    monkeypatch.setattr(dispatch, "worktree_snapshot", lambda *a: {})
    monkeypatch.setattr(dispatch, "defender_fingerprint", lambda: "unchanged")
    for name in ("reject_existing_artifacts", "verify_isolated_workspace", "set_conversation_settings",
                 "send_metadata_initialization", "wait_for_metadata_initialization",
                 "verify_metadata_worktree_unchanged", "send_worker_brief", "reverify_desktop_thread"):
        monkeypatch.setattr(dispatch, name, lambda *a: None)
    monkeypatch.setattr(dispatch, "wait_for_worker", lambda *a: {})
    calls = []

    def verify(*args):
        calls.append("verify")
        return {"number": 1, "url": "https://example.test/pr/1"}, ("b" if drift and len(calls) == 2 else "a") * 40

    monkeypatch.setattr(dispatch, "verify_worker_pr", verify)
    monkeypatch.setattr(dispatch, "git", lambda *a: "a" * 40)
    monkeypatch.setattr(dispatch, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(dispatch, "run_testing_policy", lambda *a: pytest.fail("must use budgeted gate"))
    monkeypatch.setattr(dispatch, "cleanup_session_confirmed", lambda *a: cleanup_ok)
    output = tmp_path / "result.txt"
    assert dispatch.execute(tmp_path / "event.json", output) == (1 if expected else 0)
    assert len(calls) == 2
    if expected:
        assert expected in output.read_text()
        assert "REVIEW_REQUESTED" not in output.read_text()
    else:
        event = json.loads(output.read_text())
        assert validate_document(event).valid
        assert event["event_type"] == "REVIEW_REQUESTED"
        assert [r["status"] for r in event["payload"]["controller_tests"]] == ["PASSED", "REUSED"]
