import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import scripts.aictrl_pre004_dispatch as base
import scripts.aictrl_pre004_hardened_dispatch as hard


ISSUE_NUMBER = 20
TASK_ID = "CTRL-PROD-001"
MODEL = "gpt-5.6-terra"
BRANCH = "ctrl/prod-001-generic-task-dispatch"
SESSION_NAME = "prod001-bootstrap"
READY_MARKER = "AICTRL_CTRL_PROD001_READY_V1"
EXPECTED_COMMENT = "\n".join(
    [
        "AICTRL_BOOTSTRAP_PROD001_START_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        "repo: qq230849622-a11y/AI-Dev-Control-Plane",
        f"task_id: {TASK_ID}",
        f"model: {MODEL}",
    ]
)


def admit_event(path):
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise base.ProbeFailure("EVENT_UNREADABLE") from exc
    if not isinstance(event, dict):
        raise base.ProbeFailure("EVENT_BINDING_MISMATCH")
    if (
        event.get("action") != "created"
        or event.get("repository", {}).get("full_name") != base.REPOSITORY
        or event.get("sender", {}).get("login") != "qq230849622-a11y"
        or event.get("issue", {}).get("number") != ISSUE_NUMBER
        or event.get("comment", {}).get("body") != EXPECTED_COMMENT
    ):
        raise base.ProbeFailure("EVENT_BINDING_MISMATCH")


def reject_existing_artifacts(main_path):
    local = base.run(
        ["git", "show-ref", "--verify", f"refs/heads/{BRANCH}"], cwd=main_path
    )
    if local.returncode == 0:
        raise base.ProbeFailure("LOCAL_BRANCH_EXISTS")
    remote = base.run(["git", "ls-remote", "--heads", "origin", BRANCH], cwd=main_path)
    if remote.returncode != 0:
        raise base.ProbeFailure("REMOTE_BRANCH_CHECK_FAILED")
    if remote.stdout.strip():
        raise base.ProbeFailure("REMOTE_BRANCH_EXISTS")
    prs = base.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            base.REPOSITORY,
            "--head",
            BRANCH,
            "--state",
            "all",
            "--json",
            "number",
        ],
        timeout=60,
    )
    if prs.returncode != 0:
        raise base.ProbeFailure("PR_PRECHECK_FAILED")
    try:
        values = json.loads(prs.stdout)
    except json.JSONDecodeError as exc:
        raise base.ProbeFailure("PR_PRECHECK_FAILED") from exc
    if values:
        raise base.ProbeFailure("PR_ALREADY_EXISTS")


def worker_prompt():
    return f"""You are the bounded Terra implementation worker for GitHub Issue #{ISSUE_NUMBER} in {base.REPOSITORY}.

Read the complete Issue #{ISSUE_NUMBER} first using GitHub/gh and implement its CTRL-PROD-001 contract exactly. The task envelope and the issue text are authoritative. You are already in an AO-owned isolated worktree on branch {BRANCH}.

Hard constraints:
- do not change schemas/v1/**;
- do not modify the PRE-003/PRE-004 probe scripts or workflows except where Issue #20 explicitly permits no such change;
- do not weaken Defender, runner, GitHub, AO, or Codex security boundaries;
- do not use Sol or another model;
- no Goal mode, no project-level orchestration, no extra agents;
- do not merge any PR;
- keep changes inside Issue #20 allowed_scope and outside forbidden_scope;
- implement focused tests and run the requested test commands;
- commit and push branch {BRANCH};
- open exactly one non-draft PR to master;
- leave the worktree clean after push.

After the PR is open and the implementation/tests are ready for controller review, your final assistant/provider message must be exactly this single line and nothing else:
{READY_MARKER}
"""


def spawn_worker(binary):
    result = base.run(
        [
            str(binary),
            "spawn",
            "--project",
            base.PROJECT_ID,
            "--issue",
            str(ISSUE_NUMBER),
            "--tracker-provider",
            "github",
            "--harness",
            "codex",
            "--model",
            MODEL,
            "--mode",
            "chat",
            "--branch",
            BRANCH,
            "--name",
            SESSION_NAME,
            "--prompt",
            worker_prompt(),
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise base.ProbeFailure("AO_SPAWN_FAILED")
    match = re.search(r"spawned session\s+(\S+)\s+\(", result.stdout)
    if not match:
        raise base.ProbeFailure("AO_SESSION_ID_UNAVAILABLE")
    return match.group(1)


def has_ready_marker(snapshot):
    messages = snapshot.get("messages") if isinstance(snapshot, dict) else None
    return isinstance(messages, list) and any(
        isinstance(message, dict)
        and message.get("role") == "assistant"
        and message.get("origin") == "provider"
        and message.get("text") == READY_MARKER
        for message in messages
    )


def wait_for_worker(status, session_id):
    model_verified = False
    for _ in range(240):
        session_doc = base.api_document(
            status, f"/api/v1/sessions/{quote(session_id, safe='')}"
        )
        session = session_doc.get("session") if isinstance(session_doc, dict) else None
        if isinstance(session, dict) and session.get("harness") not in (None, "codex"):
            raise base.ProbeFailure("HARNESS_SUBSTITUTION")
        snapshot = base.api_document(
            status,
            f"/api/v1/sessions/{quote(session_id, safe='')}/conversation?limit=100",
        )
        if isinstance(snapshot, dict):
            if snapshot.get("sessionId") != session_id or snapshot.get("harness") != "codex":
                raise base.ProbeFailure("CONVERSATION_BINDING_MISMATCH")
            settings = snapshot.get("settings")
            selected = settings.get("model") if isinstance(settings, dict) else None
            if selected:
                if selected != MODEL:
                    raise base.ProbeFailure("MODEL_SUBSTITUTION")
                model_verified = True
            if model_verified and has_ready_marker(snapshot):
                return
        if isinstance(session, dict) and session.get("status") == "terminated":
            raise base.ProbeFailure("WORKER_TERMINATED_BEFORE_READY")
        time.sleep(5)
    raise base.ProbeFailure("WORKER_TIMEOUT")


def verify_worker_pr(workspace, main_path, synced_head):
    if base.git(main_path, "rev-parse", "HEAD") != synced_head:
        raise base.ProbeFailure("MAIN_CHANGED")
    if base.git(main_path, "status", "--porcelain"):
        raise base.ProbeFailure("MAIN_DIRTY_AFTER_WORKER")
    if base.git(workspace, "branch", "--show-current") != BRANCH:
        raise base.ProbeFailure("WORKER_BRANCH_MISMATCH")
    if base.git(workspace, "status", "--porcelain"):
        raise base.ProbeFailure("WORKER_WORKTREE_DIRTY")

    worker_head = base.git(workspace, "rev-parse", "HEAD")
    remote = base.run(["git", "ls-remote", "--heads", "origin", BRANCH], cwd=workspace)
    if remote.returncode != 0:
        raise base.ProbeFailure("REMOTE_BRANCH_CHECK_FAILED")
    parts = remote.stdout.strip().split()
    if len(parts) < 1 or parts[0] != worker_head:
        raise base.ProbeFailure("REMOTE_BRANCH_HEAD_MISMATCH")

    result = base.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            base.REPOSITORY,
            "--head",
            BRANCH,
            "--state",
            "all",
            "--json",
            "number,url,isDraft,state,headRefName,baseRefName",
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise base.ProbeFailure("PR_LOOKUP_FAILED")
    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise base.ProbeFailure("PR_LOOKUP_FAILED") from exc
    if len(prs) != 1:
        raise base.ProbeFailure("PR_COUNT_MISMATCH")
    pr = prs[0]
    if (
        pr.get("state") != "OPEN"
        or pr.get("isDraft") is not False
        or pr.get("headRefName") != BRANCH
        or pr.get("baseRefName") != "master"
    ):
        raise base.ProbeFailure("PR_STATE_MISMATCH")

    focused = base.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_task_dispatch.py"],
        cwd=workspace,
        timeout=300,
    )
    if focused.returncode != 0:
        raise base.ProbeFailure("FOCUSED_TESTS_FAILED")
    full = base.run([sys.executable, "-m", "pytest", "-q"], cwd=workspace, timeout=600)
    if full.returncode != 0:
        raise base.ProbeFailure("FULL_TESTS_FAILED")
    return pr, worker_head


def write_result(path, status, reason="", session_id="", pr=None, worker_head=""):
    lines = [
        "AICTRL_BOOTSTRAP_PROD001_RESULT_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        f"repo: {base.REPOSITORY}",
        f"task_id: {TASK_ID}",
        f"model: {MODEL}",
        f"session_id: {session_id}",
        f"worker_head: {worker_head}",
        f"pr_number: {pr.get('number') if isinstance(pr, dict) else ''}",
        f"pr_url: {pr.get('url') if isinstance(pr, dict) else ''}",
        f"reason: {reason}",
        f"status: {status}",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def execute(event_path, result_path):
    session_id = ""
    binary = None
    status = None
    failure = None
    pass_data = None
    try:
        admit_event(event_path)
        defender_before = base.defender_fingerprint()
        binary = base.ao_binary()
        if not binary.is_file():
            raise base.ProbeFailure("AO_BINARY_UNAVAILABLE")
        status = base.ensure_ao_ready(binary)
        project_doc = base.command_json(binary, "project", "get", base.PROJECT_ID, "--json")
        project = project_doc.get("project") if isinstance(project_doc, dict) else None
        if not isinstance(project, dict):
            raise base.ProbeFailure("AO_PROJECT_UNAVAILABLE")
        main_path, synced_head = base.safe_sync_main(project)
        reject_existing_artifacts(main_path)
        if not base.has_chatgpt_login():
            raise base.ProbeFailure("CODEX_CHATGPT_LOGIN_UNVERIFIED")
        catalog = base.api_document(
            status, f"/api/v1/agents/codex/models?projectId={base.PROJECT_ID}"
        )
        if not base.catalog_has_terra(catalog):
            raise base.ProbeFailure("TERRA_MODEL_CATALOG_UNAVAILABLE")
        session_id = spawn_worker(binary)
        workspace = base.workspace_path(status, session_id)
        hard.verify_isolated_workspace(workspace, main_path)
        wait_for_worker(status, session_id)
        pr, worker_head = verify_worker_pr(workspace, main_path, synced_head)
        if base.defender_fingerprint() != defender_before:
            raise base.ProbeFailure("DEFENDER_NEW_DETECTION")
        pass_data = (pr, worker_head)
    except base.ProbeFailure as exc:
        failure = exc.code
    except Exception:
        failure = "UNEXPECTED_RUNTIME_ERROR"

    if session_id:
        if binary is None or status is None or not hard.cleanup_session_confirmed(binary, status, session_id):
            failure = "SESSION_CLEANUP_FAILED"

    if failure:
        write_result(result_path, "FAIL", failure, session_id)
        return 1
    pr, worker_head = pass_data
    write_result(result_path, "PASS", "none", session_id, pr, worker_head)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    return execute(args.event, args.result)


if __name__ == "__main__":
    raise SystemExit(main())
