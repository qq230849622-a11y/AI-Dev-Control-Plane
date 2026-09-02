"""One-shot bounded Terra R2 for PRE-006: prevent AO issue auto-start before metadata gate."""

import re
from pathlib import Path

import scripts.aictrl_bootstrap_prod001_r1 as r1


r1.ISSUE_NUMBER = 48
r1.PR_NUMBER = 51
r1.TASK_ID = "PRE-006-FIX-R2"
r1.MODEL = "gpt-5.6-terra"
r1.REASONING = "medium"
r1.PROJECT_ID = "ai-dev-control-plane"
r1.BRANCH = "fix/pre006-provider-title-proof"
r1.EXPECTED_HEAD = "50e7ebf83aac44b053be4ef30b5ca7d06a23d9b1"
r1.SESSION_NAME = "pre006-fix-r2"
r1.READY_MARKER = "AICTRL_PRE006_PROVIDER_TITLE_R2_READY_V1"
r1.ORIGINAL_BASE = "8c0191f5247d7ad0ac8d101b2661e3c9d4e9eb7d"
r1.ALLOWED_FILES = {
    "scripts/aictrl_task_dispatch.py",
    "tests/test_task_dispatch.py",
}
r1.EXPECTED_COMMENT = "\n".join(
    [
        "AICTRL_PRE006_PROVIDER_TITLE_R2_START_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        "repo: qq230849622-a11y/AI-Dev-Control-Plane",
        "task_id: PRE-006-FIX-R2",
        "pr_number: 51",
        f"expected_head: {r1.EXPECTED_HEAD}",
        f"model: {r1.MODEL}",
    ]
)


def spawn_idle_worker(binary):
    """Spawn no issue/prompt so AO cannot auto-start substantive work."""
    result = r1.base.run(
        [
            str(binary),
            "spawn",
            "--project",
            r1.PROJECT_ID,
            "--harness",
            "codex",
            "--model",
            r1.MODEL,
            "--mode",
            "chat",
            "--name",
            r1.SESSION_NAME,
        ],
        timeout=120,
    )
    if result.returncode != 0:
        r1.fail("AO_IDLE_SPAWN_FAILED")
    match = re.search(r"spawned session\s+(\S+)\s+\(", result.stdout)
    if not match:
        r1.fail("AO_SESSION_ID_UNAVAILABLE")
    return match.group(1)


def worker_prompt():
    return f"""Bounded R2 review repair for PR #51 at `{r1.EXPECTED_HEAD}`.

Fix only this controller P1 in `scripts/aictrl_task_dispatch.py` and focused tests in `tests/test_task_dispatch.py`:
- Production `spawn_worker` must create an idle/freeform AO Codex chat with explicit project/model/mode/branch/name but MUST NOT pass `--issue`, `--tracker-provider`, or any substantive `--prompt`. Pinned AO v0.12.10 auto-builds `Work on issue ...` when `IssueID` is present and prompt is empty, which can start implementation before the metadata/title gate.
- After idle spawn, preserve the accepted sequence: set+verify model/reasoning -> harmless metadata-init turn -> exact marker -> provider-native conversation title PUT + title/conversationId verification -> real bounded worker brief that references the already-validated controller Issue -> worker -> tests/PR verification -> post-worker title/conversationId reverification.
- Add a regression test that inspects the spawn command and proves `--issue`, `--tracker-provider`, and `--prompt` are absent while explicit model/mode/branch/name remain.
- Make the metadata-init response instruction unambiguous: the required marker should appear on its own line and the provider must return exactly `AICTRL_THREAD_READY_V1` with no punctuation or extra text.

Preserve every other R1 change and existing fail-closed boundary. Modify only those two files. No schemas, AO, workflows, README, or unrelated code. Run targeted checks as useful; the controller bridge runs focused/full pytest afterward. Commit/push to `{r1.BRANCH}`; keep PR #51 open; do not merge.

Final provider response exactly:
{r1.READY_MARKER}
"""


_original_verify_after = r1.verify_after


def verify_after(workspace, main_path, synced_head):
    worker_head, pr = _original_verify_after(workspace, main_path, synced_head)
    delta = {
        line.strip()
        for line in r1.base.git(workspace, "diff", "--name-only", f"{r1.EXPECTED_HEAD}...{worker_head}").splitlines()
        if line.strip()
    }
    if delta != {"scripts/aictrl_task_dispatch.py", "tests/test_task_dispatch.py"}:
        r1.fail("R2_SCOPE_MISMATCH")
    return worker_head, pr


def write_result(path, status, reason, session_id="", worker_head="", pr=None):
    lines = [
        "AICTRL_PRE006_PROVIDER_TITLE_R2_RESULT_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        f"repo: {r1.base.REPOSITORY}",
        "task_id: PRE-006-FIX-R2",
        f"model: {r1.MODEL}",
        f"session_id: {session_id}",
        f"worker_head: {worker_head}",
        f"pr_number: {pr.get('number') if isinstance(pr, dict) else ''}",
        f"pr_url: {pr.get('url') if isinstance(pr, dict) else ''}",
        f"reason: {reason}",
        f"status: {status}",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


r1.spawn_worker = spawn_idle_worker
r1.worker_prompt = worker_prompt
r1.verify_after = verify_after
r1.write_result = write_result


if __name__ == "__main__":
    raise SystemExit(r1.main())
