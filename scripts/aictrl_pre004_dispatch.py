import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen


REPOSITORY = "qq230849622-a11y/AI-Dev-Control-Plane"
PROJECT_ID = "ai-dev-control-plane"
ISSUE_NUMBER = 15
MODEL = "gpt-5.6-terra"
BRANCH = "pre/004-ao-write-probe"
SESSION_NAME = "pre004-write-probe"
READY_MARKER = "AICTRL_PRE004_READY_V1"
PROBE_FILE = "docs/PRE004_WRITE_PROBE.md"
PROBE_CONTENT = (
    "AICTRL PRE-004 WRITE PROBE\n"
    "This file exists only to prove the event-driven AO write-to-PR path.\n"
)
EXPECTED_BODY = "\n".join(
    [
        "AICTRL_PRE004_START_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        f"repo: {REPOSITORY}",
        "task_id: PRE-004",
        f"model: {MODEL}",
    ]
)


class ProbeFailure(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def run(command, cwd=None, env=None, timeout=120):
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeFailure("PROCESS_EXECUTION_FAILED") from exc


def require_success(result, code):
    if result.returncode != 0:
        raise ProbeFailure(code)
    return result.stdout.strip()


def git(repo, *args, code="GIT_COMMAND_FAILED"):
    return require_success(run(["git", *args], cwd=repo), code)


def ao_binary():
    configured = os.environ.get("AO_BIN")
    if configured:
        return Path(configured)
    found = shutil.which("ao")
    if found:
        return Path(found)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (
            Path(local_app_data)
            / "Programs"
            / "agent-orchestrator"
            / "resources"
            / "daemon"
            / "ao.exe"
        )
    return Path()


def command_json(binary, *arguments):
    result = run([str(binary), *arguments])
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def command_success(binary, *arguments):
    return run([str(binary), *arguments]).returncode == 0


def is_ready(status):
    return (
        isinstance(status, dict)
        and status.get("ready") == "ready"
        and status.get("health") == "ok"
    )


def ensure_ao_ready(binary):
    if is_ready(command_json(binary, "status", "--json")):
        return command_json(binary, "status", "--json")
    if not command_success(binary, "start"):
        raise ProbeFailure("AO_NOT_READY")
    for _ in range(15):
        time.sleep(2)
        status = command_json(binary, "status", "--json")
        if is_ready(status):
            return status
    raise ProbeFailure("AO_NOT_READY")


def api_document(status, path):
    port = status.get("port") if isinstance(status, dict) else None
    if not isinstance(port, int):
        return None
    try:
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def normalized_repo_url(value):
    if not isinstance(value, str):
        return ""
    return value.rstrip("/").removesuffix(".git").lower()


def admit_event(event_path):
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeFailure("EVENT_UNREADABLE") from exc
    if not isinstance(event, dict):
        raise ProbeFailure("EVENT_BINDING_MISMATCH")
    if (
        event.get("action") != "created"
        or event.get("repository", {}).get("full_name") != REPOSITORY
        or event.get("sender", {}).get("login") != "qq230849622-a11y"
        or event.get("issue", {}).get("number") != ISSUE_NUMBER
        or event.get("comment", {}).get("body") != EXPECTED_BODY
    ):
        raise ProbeFailure("EVENT_BINDING_MISMATCH")


def defender_fingerprint():
    script = (
        "$ErrorActionPreference='Stop'; "
        "Get-MpThreatDetection | ForEach-Object { "
        "('{0}|{1}|{2}' -f $_.ThreatID,$_.InitialDetectionTime.ToUniversalTime().Ticks,$_.ActionSuccess) "
        "} | Sort-Object"
    )
    result = run(["powershell.exe", "-NoProfile", "-Command", script], timeout=60)
    if result.returncode != 0:
        raise ProbeFailure("DEFENDER_STATUS_UNAVAILABLE")
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def has_chatgpt_login():
    result = run(["codex", "login", "status"], timeout=30)
    if result.returncode != 0:
        return False
    values = {result.stdout.strip(), result.stderr.strip()}
    return "Logged in using ChatGPT" in values


def catalog_has_terra(catalog):
    return (
        isinstance(catalog, dict)
        and catalog.get("agentId") == "codex"
        and isinstance(catalog.get("models"), list)
        and any(
            isinstance(item, dict) and item.get("id") == MODEL
            for item in catalog["models"]
        )
    )


def safe_sync_main(project):
    project_path = Path(project.get("path", ""))
    if not project_path.is_dir():
        raise ProbeFailure("PROJECT_PATH_UNAVAILABLE")
    if normalized_repo_url(project.get("repo")) != normalized_repo_url(
        f"https://github.com/{REPOSITORY}.git"
    ):
        raise ProbeFailure("PROJECT_REPOSITORY_MISMATCH")

    if git(project_path, "status", "--porcelain", code="MAIN_STATUS_FAILED"):
        raise ProbeFailure("MAIN_DIRTY")
    if git(project_path, "branch", "--show-current", code="MAIN_BRANCH_FAILED") != "master":
        raise ProbeFailure("MAIN_NOT_MASTER")
    origin_url = git(project_path, "remote", "get-url", "origin", code="ORIGIN_UNAVAILABLE")
    if normalized_repo_url(origin_url) != normalized_repo_url(
        f"https://github.com/{REPOSITORY}.git"
    ):
        raise ProbeFailure("ORIGIN_MISMATCH")

    git(project_path, "fetch", "--prune", "origin", "master", code="FETCH_FAILED")
    local_head = git(project_path, "rev-parse", "HEAD", code="MAIN_HEAD_FAILED")
    origin_head = git(
        project_path, "rev-parse", "origin/master", code="ORIGIN_HEAD_FAILED"
    )
    ancestor = run(
        ["git", "merge-base", "--is-ancestor", local_head, origin_head], cwd=project_path
    )
    if ancestor.returncode != 0:
        raise ProbeFailure("MAIN_DIVERGED")
    git(project_path, "merge", "--ff-only", "origin/master", code="FAST_FORWARD_FAILED")
    synced_head = git(project_path, "rev-parse", "HEAD", code="MAIN_HEAD_FAILED")
    if synced_head != origin_head:
        raise ProbeFailure("FAST_FORWARD_MISMATCH")
    if git(project_path, "status", "--porcelain", code="MAIN_STATUS_FAILED"):
        raise ProbeFailure("MAIN_DIRTY_AFTER_SYNC")
    return project_path, synced_head


def git_common_dir(path):
    value = git(
        path,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
        code="GIT_COMMON_DIR_FAILED",
    )
    return Path(value).resolve()


def workspace_path(status, session_id):
    document = api_document(
        status, f"/api/v1/desktop/sessions/{quote(session_id, safe='')}/workspace"
    )
    value = document.get("workspacePath") if isinstance(document, dict) else None
    return Path(value) if isinstance(value, str) and value else None


def verify_isolated_workspace(workspace, main_path):
    if (
        not workspace
        or not workspace.is_dir()
        or workspace.resolve() == main_path.resolve()
    ):
        raise ProbeFailure("WORKTREE_UNVERIFIED")
    if git(workspace, "rev-parse", "--is-inside-work-tree") != "true":
        raise ProbeFailure("WORKTREE_UNVERIFIED")
    if git_common_dir(workspace) != git_common_dir(main_path):
        raise ProbeFailure("WORKTREE_REPOSITORY_MISMATCH")


def reject_existing_probe_artifacts(main_path):
    local = run(["git", "show-ref", "--verify", f"refs/heads/{BRANCH}"], cwd=main_path)
    if local.returncode == 0:
        raise ProbeFailure("LOCAL_BRANCH_EXISTS")
    remote = run(["git", "ls-remote", "--heads", "origin", BRANCH], cwd=main_path)
    if remote.returncode != 0:
        raise ProbeFailure("REMOTE_BRANCH_CHECK_FAILED")
    if remote.stdout.strip():
        raise ProbeFailure("REMOTE_BRANCH_EXISTS")
    prs = run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            REPOSITORY,
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
        raise ProbeFailure("PR_PRECHECK_FAILED")
    try:
        values = json.loads(prs.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeFailure("PR_PRECHECK_FAILED") from exc
    if values:
        raise ProbeFailure("PR_ALREADY_EXISTS")


def worker_prompt():
    return f"""PRE-004 bounded write-to-PR probe for {REPOSITORY}.
Read GitHub Issue #{ISSUE_NUMBER} first and obey it exactly.
You are already in an AO-owned isolated worktree on branch {BRANCH}.

Allowed repository change: create only {PROBE_FILE} with exactly these two lines:
AICTRL PRE-004 WRITE PROBE
This file exists only to prove the event-driven AO write-to-PR path.

Then:
1. Verify git diff/status shows no repository change except {PROBE_FILE}.
2. Commit exactly once with message: PRE-004: prove AO write-to-PR path
3. Push only branch {BRANCH} to origin.
4. Create exactly one non-draft PR to master using gh. Do not merge it.
5. Do not edit workflows, source, tests, config, or any other file.
6. Do not switch model or start another agent.
7. Your final assistant message must be exactly: {READY_MARKER}
"""


def spawn_worker(binary):
    result = run(
        [
            str(binary),
            "spawn",
            "--project",
            PROJECT_ID,
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
        raise ProbeFailure("AO_SPAWN_FAILED")
    match = re.search(r"spawned session\s+(\S+)\s+\(", result.stdout)
    if not match:
        raise ProbeFailure("AO_SESSION_ID_UNAVAILABLE")
    return match.group(1)


def bound_terra_conversation(snapshot, session_id):
    return (
        isinstance(snapshot, dict)
        and snapshot.get("sessionId") == session_id
        and snapshot.get("harness") == "codex"
        and isinstance(snapshot.get("settings"), dict)
        and snapshot["settings"].get("model") == MODEL
    )


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
    for _ in range(180):
        session_doc = api_document(
            status, f"/api/v1/sessions/{quote(session_id, safe='')}"
        )
        session = session_doc.get("session") if isinstance(session_doc, dict) else None
        if not isinstance(session, dict):
            time.sleep(5)
            continue
        if session.get("harness") != "codex":
            raise ProbeFailure("HARNESS_SUBSTITUTION")

        snapshot = api_document(
            status,
            f"/api/v1/sessions/{quote(session_id, safe='')}/conversation?limit=100",
        )
        if isinstance(snapshot, dict):
            if snapshot.get("sessionId") != session_id or snapshot.get("harness") != "codex":
                raise ProbeFailure("CONVERSATION_BINDING_MISMATCH")
            settings = snapshot.get("settings")
            selected = settings.get("model") if isinstance(settings, dict) else None
            if selected:
                if selected != MODEL:
                    raise ProbeFailure("MODEL_SUBSTITUTION")
                model_verified = True
            if model_verified and has_ready_marker(snapshot):
                return

        if session.get("status") == "terminated":
            raise ProbeFailure("WORKER_TERMINATED_BEFORE_READY")
        time.sleep(5)
    raise ProbeFailure("WORKER_TIMEOUT")


def verify_pr_and_git(workspace, main_path, synced_head):
    if git(main_path, "rev-parse", "HEAD") != synced_head:
        raise ProbeFailure("MAIN_CHANGED")
    if git(main_path, "status", "--porcelain"):
        raise ProbeFailure("MAIN_DIRTY_AFTER_WORKER")
    if git(workspace, "branch", "--show-current") != BRANCH:
        raise ProbeFailure("WORKER_BRANCH_MISMATCH")
    if git(workspace, "status", "--porcelain"):
        raise ProbeFailure("WORKER_WORKTREE_DIRTY")

    names = [
        line.strip()
        for line in git(
            workspace,
            "diff",
            "--name-only",
            "origin/master...HEAD",
            code="WORKER_DIFF_FAILED",
        ).splitlines()
        if line.strip()
    ]
    if names != [PROBE_FILE]:
        raise ProbeFailure("WORKER_SCOPE_VIOLATION")
    if git(workspace, "rev-list", "--count", "origin/master..HEAD") != "1":
        raise ProbeFailure("WORKER_COMMIT_COUNT_MISMATCH")
    if git(workspace, "log", "-1", "--format=%s") != "PRE-004: prove AO write-to-PR path":
        raise ProbeFailure("WORKER_COMMIT_MESSAGE_MISMATCH")

    probe_path = workspace / PROBE_FILE
    try:
        content = probe_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError as exc:
        raise ProbeFailure("PROBE_FILE_UNREADABLE") from exc
    if content != PROBE_CONTENT:
        raise ProbeFailure("PROBE_FILE_CONTENT_MISMATCH")

    worker_head = git(workspace, "rev-parse", "HEAD")
    remote = run(["git", "ls-remote", "--heads", "origin", BRANCH], cwd=workspace)
    if remote.returncode != 0:
        raise ProbeFailure("REMOTE_BRANCH_CHECK_FAILED")
    parts = remote.stdout.strip().split()
    if len(parts) < 1 or parts[0] != worker_head:
        raise ProbeFailure("REMOTE_BRANCH_HEAD_MISMATCH")

    result = run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            REPOSITORY,
            "--head",
            BRANCH,
            "--base",
            "master",
            "--state",
            "open",
            "--json",
            "number,url,isDraft,state,headRefName,baseRefName",
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise ProbeFailure("PR_LOOKUP_FAILED")
    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeFailure("PR_LOOKUP_FAILED") from exc
    if len(prs) != 1:
        raise ProbeFailure("PR_COUNT_MISMATCH")
    pr = prs[0]
    if (
        pr.get("isDraft") is not False
        or pr.get("state") != "OPEN"
        or pr.get("headRefName") != BRANCH
        or pr.get("baseRefName") != "master"
    ):
        raise ProbeFailure("PR_STATE_MISMATCH")

    diff = run(
        ["gh", "pr", "diff", str(pr["number"]), "--repo", REPOSITORY, "--name-only"],
        timeout=60,
    )
    if diff.returncode != 0:
        raise ProbeFailure("PR_DIFF_LOOKUP_FAILED")
    pr_names = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    if pr_names != [PROBE_FILE]:
        raise ProbeFailure("PR_SCOPE_VIOLATION")
    return pr, worker_head


def write_fail(path, code, session_id=""):
    body = "\n".join(
        [
            "AICTRL_PRE004_FAIL_V1",
            "project_key: AI_DEV_CONTROL_PLANE",
            f"repo: {REPOSITORY}",
            "task_id: PRE-004",
            f"model: {MODEL}",
            f"session_id: {session_id}",
            f"reason: {code}",
            "status: FAIL",
        ]
    )
    Path(path).write_text(body + "\n", encoding="utf-8")


def write_pass(path, session_id, pr, main_head, worker_head):
    body = "\n".join(
        [
            "AICTRL_PRE004_RESULT_V1",
            "project_key: AI_DEV_CONTROL_PLANE",
            f"repo: {REPOSITORY}",
            "task_id: PRE-004",
            "runner: AICTRL-WIN11",
            f"model: {MODEL}",
            f"session_id: {session_id}",
            f"worker_head: {worker_head}",
            f"main_head: {main_head}",
            f"branch: {BRANCH}",
            f"pr_number: {pr['number']}",
            f"pr_url: {pr['url']}",
            f"result_marker: {READY_MARKER}",
            "main_unchanged_after_sync: true",
            "worktree_isolated: true",
            "defender_new_detection: false",
            "status: PASS",
        ]
    )
    Path(path).write_text(body + "\n", encoding="utf-8")


def execute(event_path, result_path):
    session_id = ""
    defender_before = None
    binary = None
    try:
        admit_event(event_path)
        defender_before = defender_fingerprint()
        binary = ao_binary()
        if not binary.is_file():
            raise ProbeFailure("AO_BINARY_UNAVAILABLE")
        status = ensure_ao_ready(binary)

        project_doc = command_json(binary, "project", "get", PROJECT_ID, "--json")
        project = project_doc.get("project") if isinstance(project_doc, dict) else None
        if not isinstance(project, dict):
            raise ProbeFailure("AO_PROJECT_UNAVAILABLE")
        main_path, synced_head = safe_sync_main(project)
        reject_existing_probe_artifacts(main_path)

        if not has_chatgpt_login():
            raise ProbeFailure("CODEX_CHATGPT_LOGIN_UNVERIFIED")
        catalog = api_document(
            status,
            f"/api/v1/agents/codex/models?projectId={quote(PROJECT_ID, safe='')}",
        )
        if not catalog_has_terra(catalog):
            raise ProbeFailure("TERRA_MODEL_CATALOG_UNAVAILABLE")

        session_id = spawn_worker(binary)
        workspace = workspace_path(status, session_id)
        verify_isolated_workspace(workspace, main_path)
        wait_for_worker(status, session_id)
        pr, worker_head = verify_pr_and_git(workspace, main_path, synced_head)

        defender_after = defender_fingerprint()
        if defender_after != defender_before:
            raise ProbeFailure("DEFENDER_NEW_DETECTION")
        write_pass(result_path, session_id, pr, synced_head, worker_head)
        return 0
    except ProbeFailure as exc:
        write_fail(result_path, exc.code, session_id)
        return 1
    except Exception:
        write_fail(result_path, "UNEXPECTED_RUNTIME_ERROR", session_id)
        return 1
    finally:
        if binary is not None and session_id:
            try:
                command_success(binary, "session", "kill", session_id, "--project", PROJECT_ID)
            except Exception:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    return execute(args.event, args.result)


if __name__ == "__main__":
    sys.exit(main())
