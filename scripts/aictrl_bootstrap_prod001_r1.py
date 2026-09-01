import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import scripts.aictrl_pre004_dispatch as base
import scripts.aictrl_pre004_hardened_dispatch as hard


ISSUE_NUMBER = 20
PR_NUMBER = 23
TASK_ID = "CTRL-PROD-001"
MODEL = "gpt-5.6-terra"
REASONING = "medium"
PROJECT_ID = "ai-dev-control-plane"
BRANCH = "ctrl/prod-001-generic-task-dispatch"
EXPECTED_HEAD = "53565c28840f3d1cd9585934b319c1d5f9278701"
SESSION_NAME = "prod001-r1"
READY_MARKER = "AICTRL_CTRL_PROD001_R1_READY_V1"
ALLOWED_FILES = {
    ".github/workflows/aictrl-task-dispatch.yml",
    "scripts/aictrl_task_dispatch.py",
    "tests/test_task_dispatch.py",
}
EXPECTED_COMMENT = "\n".join(
    [
        "AICTRL_BOOTSTRAP_PROD001_R1_START_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        "repo: qq230849622-a11y/AI-Dev-Control-Plane",
        f"task_id: {TASK_ID}",
        f"pr_number: {PR_NUMBER}",
        f"expected_head: {EXPECTED_HEAD}",
        f"model: {MODEL}",
    ]
)


class R1Failure(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def fail(code):
    raise R1Failure(code)


def admit_event(path):
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise R1Failure("EVENT_UNREADABLE") from exc
    if not isinstance(event, dict):
        fail("EVENT_BINDING_MISMATCH")
    if (
        event.get("action") != "created"
        or event.get("repository", {}).get("full_name") != base.REPOSITORY
        or event.get("sender", {}).get("login") != "qq230849622-a11y"
        or event.get("issue", {}).get("number") != ISSUE_NUMBER
        or event.get("comment", {}).get("body") != EXPECTED_COMMENT
    ):
        fail("EVENT_BINDING_MISMATCH")


def gh_json(arguments, code):
    result = base.run(["gh", *arguments], timeout=60)
    if result.returncode != 0:
        fail(code)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise R1Failure(code) from exc
    return value


def verify_pr_before():
    pr = gh_json(
        [
            "pr",
            "view",
            str(PR_NUMBER),
            "--repo",
            base.REPOSITORY,
            "--json",
            "number,url,isDraft,state,headRefName,baseRefName,headRefOid",
        ],
        "PR_LOOKUP_FAILED",
    )
    if (
        not isinstance(pr, dict)
        or pr.get("number") != PR_NUMBER
        or pr.get("state") != "OPEN"
        or pr.get("isDraft") is not False
        or pr.get("headRefName") != BRANCH
        or pr.get("baseRefName") != "master"
        or pr.get("headRefOid") != EXPECTED_HEAD
    ):
        fail("PR_BINDING_MISMATCH")
    return pr


def worker_prompt():
    return f"""You are the bounded Terra review-rework worker for {base.REPOSITORY} PR #{PR_NUMBER} / Issue #{ISSUE_NUMBER}.

Read the latest controller review on PR #{PR_NUMBER}, specifically `CTRL-PROD-001_REVIEW: CHANGES_REQUIRED`, and fix exactly those bounded items on the existing branch `{BRANCH}`:
1. make `scripts/aictrl_task_dispatch.py` self-contained for the repo src-layout by adding `<repo>/src` before importing `aictrl`, plus a real subprocess regression for the actual script entry;
2. separate the controller Actions token from target-repository `gh` auth: controller Issue/comment reads may use the workflow token, while target-repo PR operations must explicitly clear `GH_TOKEN` and `GITHUB_TOKEN` so host-user `gh` auth is used; add tests;
3. serialize V1 dispatches at the shared local-main boundary with a fixed/project-scoped concurrency group rather than comment-id concurrency;
4. generate a deterministic AO display name <=20 chars for arbitrary valid task IDs, with long-ID tests;
5. remove the instruction that makes the worker rerun the canonical testing_policy; controller owns those final policy tests. Worker may run targeted checks needed to implement/debug.

Preserve all other accepted CTRL-PROD-001 boundaries: exact validation/routing, Luna/Terra-only policy, stale HEAD checks, structured model+reasoning settings, isolated worktree, result binding, scope verification, Defender gate, cleanup, no PR/fork trigger, no force/reset/rebase, no Sol, no auto-merge.

Modify only the existing CTRL-PROD-001 PR files unless a focused test genuinely requires another file already allowed by Issue #20. Do not change schemas, PRE-003/PRE-004 files, bootstrap files, or unrelated code. Commit and push to `{BRANCH}`. Keep PR #{PR_NUMBER} open and non-draft. Do not merge it. Do not start follow-on work.

Run targeted checks as needed. The controller will run the canonical focused/full tests after your final commit.

When the rework is complete and pushed, your final assistant/provider message must be exactly this single line and nothing else:
{READY_MARKER}
"""


def api_patch(status, path, payload):
    port = status.get("port") if isinstance(status, dict) else None
    if not isinstance(port, int):
        return None
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            value = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def spawn_and_claim(binary):
    result = base.run(
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
            "--name",
            SESSION_NAME,
        ],
        timeout=120,
    )
    if result.returncode != 0:
        fail("AO_SPAWN_FAILED")
    match = re.search(r"spawned session\s+(\S+)\s+\(", result.stdout)
    if not match:
        fail("AO_SESSION_ID_UNAVAILABLE")
    session_id = match.group(1)
    claim = base.run(
        [
            str(binary),
            "session",
            "claim-pr",
            session_id,
            str(PR_NUMBER),
            "--project",
            PROJECT_ID,
            "--no-takeover",
            "--json",
        ],
        timeout=120,
    )
    if claim.returncode != 0:
        try:
            base.command_success(binary, "session", "kill", session_id, "--project", PROJECT_ID)
        finally:
            fail("PR_CLAIM_FAILED")
    try:
        document = json.loads(claim.stdout)
    except json.JSONDecodeError:
        fail("PR_CLAIM_UNVERIFIED")
    prs = document.get("prs") if isinstance(document, dict) else None
    if not isinstance(prs, list) or len(prs) != 1 or prs[0].get("number") != PR_NUMBER:
        fail("PR_CLAIM_UNVERIFIED")
    return session_id


def set_settings(status, session_id):
    path = f"/api/v1/sessions/{quote(session_id, safe='')}/conversation/settings"
    if api_patch(status, path, {"model": MODEL, "reasoningEffort": REASONING}) is None:
        fail("CONVERSATION_SETTINGS_FAILED")
    for _ in range(10):
        snapshot = base.api_document(
            status,
            f"/api/v1/sessions/{quote(session_id, safe='')}/conversation?limit=100",
        )
        settings = snapshot.get("settings") if isinstance(snapshot, dict) else None
        if (
            isinstance(snapshot, dict)
            and snapshot.get("sessionId") == session_id
            and snapshot.get("harness") == "codex"
            and isinstance(settings, dict)
            and settings.get("model") == MODEL
            and settings.get("reasoningEffort") == REASONING
        ):
            return
        time.sleep(0.5)
    fail("CONVERSATION_SETTINGS_UNVERIFIED")


def send_prompt(binary, session_id):
    if not base.command_success(binary, "send", "--session", session_id, "--message", worker_prompt()):
        fail("WORKER_PROMPT_SEND_FAILED")


def wait_ready(status, session_id):
    for _ in range(240):
        session_doc = base.api_document(status, f"/api/v1/sessions/{quote(session_id, safe='')}")
        session = session_doc.get("session") if isinstance(session_doc, dict) else None
        snapshot = base.api_document(
            status,
            f"/api/v1/sessions/{quote(session_id, safe='')}/conversation?limit=100",
        )
        if isinstance(snapshot, dict):
            messages = snapshot.get("messages")
            if isinstance(messages, list) and any(
                isinstance(message, dict)
                and message.get("role") == "assistant"
                and message.get("origin") == "provider"
                and message.get("text") == READY_MARKER
                for message in messages
            ):
                return
        if isinstance(session, dict) and session.get("status") == "terminated":
            fail("WORKER_TERMINATED_BEFORE_READY")
        time.sleep(5)
    fail("WORKER_TIMEOUT")


def verify_after(workspace, main_path):
    if not workspace or not workspace.is_dir():
        fail("WORKSPACE_UNAVAILABLE")
    hard.verify_isolated_workspace(workspace, main_path)
    if base.git(workspace, "branch", "--show-current") != BRANCH:
        fail("WORKER_BRANCH_MISMATCH")
    if base.git(workspace, "status", "--porcelain"):
        fail("WORKER_WORKTREE_DIRTY")
    worker_head = base.git(workspace, "rev-parse", "HEAD")
    if worker_head == EXPECTED_HEAD:
        fail("NO_CODE_DELTA")
    remote = base.run(["git", "ls-remote", "--heads", "origin", BRANCH], cwd=workspace)
    if remote.returncode != 0 or remote.stdout.strip().split(maxsplit=1)[0:1] != [worker_head]:
        fail("REMOTE_HEAD_MISMATCH")
    pr = gh_json(
        [
            "pr",
            "view",
            str(PR_NUMBER),
            "--repo",
            base.REPOSITORY,
            "--json",
            "number,url,isDraft,state,headRefName,baseRefName,headRefOid",
        ],
        "PR_LOOKUP_FAILED",
    )
    if (
        pr.get("state") != "OPEN"
        or pr.get("isDraft") is not False
        or pr.get("headRefName") != BRANCH
        or pr.get("baseRefName") != "master"
        or pr.get("headRefOid") != worker_head
    ):
        fail("PR_STATE_MISMATCH")
    changed = [
        line.strip()
        for line in base.git(workspace, "diff", "--name-only", "404bd78c21fa65bc5a0b77e763f1707f9eaa5874...HEAD").splitlines()
        if line.strip()
    ]
    if not changed or not set(changed).issubset(ALLOWED_FILES):
        fail("REWORK_SCOPE_VIOLATION")
    focused = base.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_task_dispatch.py"],
        cwd=workspace,
        timeout=600,
    )
    if focused.returncode != 0:
        fail("FOCUSED_TESTS_FAILED")
    full = base.run([sys.executable, "-m", "pytest", "-q"], cwd=workspace, timeout=900)
    if full.returncode != 0:
        fail("FULL_TESTS_FAILED")
    return worker_head, pr


def write_result(path, status, reason, session_id="", worker_head="", pr=None):
    lines = [
        "AICTRL_BOOTSTRAP_PROD001_R1_RESULT_V1",
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
    worker_head = ""
    pr = None
    try:
        admit_event(event_path)
        verify_pr_before()
        defender_before = base.defender_fingerprint()
        binary = base.ao_binary()
        if not binary.is_file():
            fail("AO_BINARY_UNAVAILABLE")
        status = base.ensure_ao_ready(binary)
        project_doc = base.command_json(binary, "project", "get", PROJECT_ID, "--json")
        project = project_doc.get("project") if isinstance(project_doc, dict) else None
        if not isinstance(project, dict):
            fail("AO_PROJECT_UNAVAILABLE")
        main_path, _ = base.safe_sync_main(project)
        if not base.has_chatgpt_login():
            fail("CODEX_CHATGPT_LOGIN_UNVERIFIED")
        catalog = base.api_document(status, f"/api/v1/agents/codex/models?projectId={PROJECT_ID}")
        if not base.catalog_has_terra(catalog):
            fail("TERRA_MODEL_CATALOG_UNAVAILABLE")
        session_id = spawn_and_claim(binary)
        workspace = base.workspace_path(status, session_id)
        hard.verify_isolated_workspace(workspace, main_path)
        if base.git(workspace, "branch", "--show-current") != BRANCH:
            fail("PR_CLAIM_BRANCH_MISMATCH")
        set_settings(status, session_id)
        send_prompt(binary, session_id)
        wait_ready(status, session_id)
        worker_head, pr = verify_after(workspace, main_path)
        if base.defender_fingerprint() != defender_before:
            fail("DEFENDER_NEW_DETECTION")
    except (R1Failure, base.ProbeFailure) as exc:
        failure = getattr(exc, "code", str(exc))
    except Exception:
        failure = "UNEXPECTED_RUNTIME_ERROR"

    if session_id:
        if binary is None or status is None or not hard.cleanup_session_confirmed(binary, status, session_id):
            failure = "SESSION_CLEANUP_FAILED"

    if failure:
        write_result(result_path, "FAIL", failure, session_id, worker_head, pr)
        return 1
    write_result(result_path, "PASS", "none", session_id, worker_head, pr)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    return execute(args.event, args.result)


if __name__ == "__main__":
    raise SystemExit(main())
