"""One-shot bounded Terra repair for PRE-006 provider-title integration."""

from pathlib import Path

import scripts.aictrl_bootstrap_prod001_r1 as r1


r1.ISSUE_NUMBER = 48
r1.PR_NUMBER = 51
r1.TASK_ID = "PRE-006-FIX"
r1.MODEL = "gpt-5.6-terra"
r1.REASONING = "medium"
r1.PROJECT_ID = "ai-dev-control-plane"
r1.BRANCH = "fix/pre006-provider-title-proof"
r1.EXPECTED_HEAD = "779d6950540f0460eaa986a597736fe28686bd37"
r1.SESSION_NAME = "pre006-fix-r1"
r1.READY_MARKER = "AICTRL_PRE006_PROVIDER_TITLE_R1_READY_V1"
r1.ORIGINAL_BASE = "8c0191f5247d7ad0ac8d101b2661e3c9d4e9eb7d"
PLACEHOLDER = "docs/PRE006_PROVIDER_TITLE_FIX_BOOTSTRAP.md"
r1.ALLOWED_FILES = {
    "scripts/aictrl_task_dispatch.py",
    "tests/test_task_dispatch.py",
    PLACEHOLDER,
}
r1.EXPECTED_COMMENT = "\n".join(
    [
        "AICTRL_PRE006_PROVIDER_TITLE_R1_START_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        "repo: qq230849622-a11y/AI-Dev-Control-Plane",
        "task_id: PRE-006-FIX",
        "pr_number: 51",
        f"expected_head: {r1.EXPECTED_HEAD}",
        f"model: {r1.MODEL}",
    ]
)


def worker_prompt():
    return f"""You are the single bounded Terra repair worker for {r1.base.REPOSITORY} PR #51 / PRE-006 Issue #48.

Fix the Codex Desktop visibility integration based on the pinned AO v0.12.10 contract. The current production dispatcher is wrong in two specific ways:
- AO v0.12.10 intentionally does NOT expose the native Codex thread id as `providerThreadId` in the public conversation snapshot, so do not require, guess, scrape SQLite for, or synthesize that id.
- `ao session rename` changes only AO's display name. It is NOT the provider-native Codex thread title path.

Implement this exact repair in `scripts/aictrl_task_dispatch.py` with focused regression coverage in `tests/test_task_dispatch.py`:
1. Keep deterministic `desktop_thread_name(task_id, model)` generation.
2. Before substantive implementation work, send one harmless bounded metadata-initialization turn through the existing AO chat session. It must instruct the provider to do no file reads, tools, commands, or edits and reply with one exact marker such as `AICTRL_THREAD_READY_V1`. Wait boundedly for that exact provider reply and fail closed on termination, model/reasoning drift, or timeout. This first turn exists only so Codex has persisted the native thread before title mutation.
3. After that marker, set the PROVIDER-NATIVE thread title using AO's supported local API `PUT /api/v1/sessions/{{sessionId}}/conversation/title` with `{{"title": <deterministic title>}}`. Do not use `ao session rename` as provider-title proof.
4. Verify through AO's public conversation snapshot that `title` exactly equals the requested deterministic title before sending the real implementation brief. Missing/mismatched title fails closed.
5. Capture AO's public durable `conversationId` from the same bound conversation snapshot as audit correlation evidence. It must be non-empty and is NOT claimed to be the native Codex thread id.
6. Send the real worker brief only after steps 2-5 succeed.
7. After the worker completes and controller tests/PR verification succeed, re-fetch the conversation snapshot and require the same provider-native `title` and the same AO `conversationId` before emitting REVIEW_REQUESTED evidence. Absence or drift fails closed.
8. Replace unsupported `provider_thread_id` review evidence with supported public evidence: keep `desktop_thread_name`, add a non-empty `conversation_id`, and an explicit boolean such as `desktop_thread_verified: true`. The event payload schema is open, so do not change `schemas/v1/event.schema.json`.
9. Tests must model the real AO v0.12.10 wire shape: `conversationId`, `sessionId`, `harness`, `settings`, and provider `title`; remove invented `providerThreadId` fixtures/expectations.
10. Preserve all existing exact routing, stale-head, Luna/Terra-only model policy, reasoning verification, isolated worktree, scope/PR/head checks, controller testing policy, Defender gate, session cleanup, no Sol, no Goal mode, and no auto-merge.

Do not modify AO, schemas, workflows, README, or unrelated files. Modify only `scripts/aictrl_task_dispatch.py` and `tests/test_task_dispatch.py`, and DELETE the bootstrap placeholder `{PLACEHOLDER}` before completion.

Run targeted tests as useful. The controller bridge will run `python -m pytest -q tests/test_task_dispatch.py` and the full suite after your commit.

Commit and push to `{r1.BRANCH}`. Keep PR #51 open/non-draft. Do not merge it. Do not start follow-on work.

Your final assistant/provider message must be exactly this single line and nothing else:
{r1.READY_MARKER}
"""


_original_verify_after = r1.verify_after


def verify_after(workspace, main_path, synced_head):
    worker_head, pr = _original_verify_after(workspace, main_path, synced_head)
    if (workspace / PLACEHOLDER).exists():
        r1.fail("BOOTSTRAP_PLACEHOLDER_NOT_REMOVED")
    delta = {
        line.strip()
        for line in r1.base.git(workspace, "diff", "--name-only", f"{r1.EXPECTED_HEAD}...{worker_head}").splitlines()
        if line.strip()
    }
    expected = {"scripts/aictrl_task_dispatch.py", "tests/test_task_dispatch.py", PLACEHOLDER}
    if delta != expected:
        r1.fail("R1_SCOPE_MISMATCH")
    return worker_head, pr


def write_result(path, status, reason, session_id="", worker_head="", pr=None):
    lines = [
        "AICTRL_PRE006_PROVIDER_TITLE_R1_RESULT_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        f"repo: {r1.base.REPOSITORY}",
        "task_id: PRE-006-FIX",
        f"model: {r1.MODEL}",
        f"session_id: {session_id}",
        f"worker_head: {worker_head}",
        f"pr_number: {pr.get('number') if isinstance(pr, dict) else ''}",
        f"pr_url: {pr.get('url') if isinstance(pr, dict) else ''}",
        f"reason: {reason}",
        f"status: {status}",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


r1.worker_prompt = worker_prompt
r1.verify_after = verify_after
r1.write_result = write_result


if __name__ == "__main__":
    raise SystemExit(r1.main())
