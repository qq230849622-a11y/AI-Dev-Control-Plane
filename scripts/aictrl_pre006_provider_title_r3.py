"""One-shot bounded Terra R3 for PRE-006 metadata-turn containment."""

import re
from pathlib import Path

import scripts.aictrl_bootstrap_prod001_r1 as r1


r1.ISSUE_NUMBER = 48
r1.PR_NUMBER = 51
r1.TASK_ID = "PRE-006-FIX-R3"
r1.MODEL = "gpt-5.6-terra"
r1.REASONING = "medium"
r1.PROJECT_ID = "ai-dev-control-plane"
r1.BRANCH = "fix/pre006-provider-title-proof"
r1.EXPECTED_HEAD = "10a6169400747ce3a6bd56187192792775086f51"
r1.SESSION_NAME = "pre006-fix-r3"
r1.READY_MARKER = "AICTRL_PRE006_PROVIDER_TITLE_R3_READY_V1"
r1.ORIGINAL_BASE = "8c0191f5247d7ad0ac8d101b2661e3c9d4e9eb7d"
r1.ALLOWED_FILES = {
    "scripts/aictrl_task_dispatch.py",
    "tests/test_task_dispatch.py",
}
r1.EXPECTED_COMMENT = "\n".join(
    [
        "AICTRL_PRE006_PROVIDER_TITLE_R3_START_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        "repo: qq230849622-a11y/AI-Dev-Control-Plane",
        "task_id: PRE-006-FIX-R3",
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
    return f"""Bounded R3 review repair for PR #51 at `{r1.EXPECTED_HEAD}`.

Fix only the remaining Codex-review P2 in `scripts/aictrl_task_dispatch.py` and focused tests in `tests/test_task_dispatch.py`:

- The metadata initialization turn is a safety gate, not implementation work. Do not accept it merely because the last provider message equals `AICTRL_THREAD_READY_V1`.
- Bind the exact marker provider message to its non-empty AO `turnId`, require the corresponding turn to be terminal/completed, and reject the metadata turn if that same turn contains tool/action activity. At minimum reject activity kinds `command`, `file_change`, `mcp_tool`, `approval`, `auto_review`, `user_input`, and `error`. Benign non-mutating `reasoning`, `usage`, and `system` activity may remain allowed.
- Independently snapshot the AO worker worktree immediately before sending the metadata initialization turn: record HEAD and clean status. Immediately after the exact marker is proven, require the same HEAD and the same clean status before setting the provider title or sending the real worker brief. Any metadata-turn git mutation fails closed.
- The real worker brief must never be sent after tool activity, missing/ambiguous marker turn identity, non-completed marker turn, or worktree mutation.
- Add focused regression tests covering: exact marker bound to one completed turn; prohibited activity on that turn fails closed; activity belonging to another turn does not create a false positive; missing/ambiguous marker turn fails closed; benign reasoning/usage is allowed; and worktree HEAD/status drift across metadata initialization fails closed.

Preserve every R2 boundary: idle/freeform spawn with no `--issue`, `--tracker-provider`, or `--prompt`; explicit model/reasoning gate; provider-native conversation title PUT; stable public AO `conversationId` correlation; post-worker title/conversationId reverification; existing scope/testing/Defender/session-cleanup protections; no Sol, no Goal mode, no auto-merge.

Modify only `scripts/aictrl_task_dispatch.py` and `tests/test_task_dispatch.py`. No schemas, AO, workflows, README, or unrelated code. Run targeted checks as useful; the controller bridge runs focused/full pytest afterward. Commit/push to `{r1.BRANCH}`; keep PR #51 open; do not merge.

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
    if not delta or not delta.issubset({"scripts/aictrl_task_dispatch.py", "tests/test_task_dispatch.py"}):
        r1.fail("R3_SCOPE_MISMATCH")
    return worker_head, pr


def write_result(path, status, reason, session_id="", worker_head="", pr=None):
    lines = [
        "AICTRL_PRE006_PROVIDER_TITLE_R3_RESULT_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        f"repo: {r1.base.REPOSITORY}",
        "task_id: PRE-006-FIX-R3",
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
