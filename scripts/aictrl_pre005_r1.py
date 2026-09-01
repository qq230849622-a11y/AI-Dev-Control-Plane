"""One-shot bounded PRE-005 review rework using the already-hardened AO PR rework bridge."""

from pathlib import Path

import scripts.aictrl_bootstrap_prod001_r1 as r1


r1.ISSUE_NUMBER = 29
r1.PR_NUMBER = 35
r1.TASK_ID = "PRE-005"
r1.MODEL = "gpt-5.6-terra"
r1.REASONING = "medium"
r1.PROJECT_ID = "ai-dev-control-plane"
r1.BRANCH = "aictrl/pre-005"
r1.EXPECTED_HEAD = "ed08ff33341d4d3f7a4d086dc65d79ae25848876"
r1.SESSION_NAME = "pre005-r1"
r1.READY_MARKER = "AICTRL_PRE005_R1_READY_V1"
r1.ORIGINAL_BASE = "9fbb91b560bebdeb037c6367c39553ae3a4f4d23"
r1.ALLOWED_FILES = {
    ".github/workflows/aictrl-task-dispatch.yml",
    "scripts/aictrl_task_dispatch.py",
    "scripts/aictrl_task_dispatch_bootstrap.py",
    "tests/test_task_dispatch.py",
}
r1.EXPECTED_COMMENT = "\n".join(
    [
        "AICTRL_PRE005_R1_START_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        "repo: qq230849622-a11y/AI-Dev-Control-Plane",
        "task_id: PRE-005",
        "pr_number: 35",
        f"expected_head: {r1.EXPECTED_HEAD}",
        f"model: {r1.MODEL}",
    ]
)


def worker_prompt():
    return f"""You are the single bounded Terra review-rework worker for PR #35 / Issue #29 in {r1.base.REPOSITORY}.

Fix exactly the controller/Codex P2 finding on the existing branch `{r1.BRANCH}` at expected HEAD `{r1.EXPECTED_HEAD}`:
- PRE-005 already establishes `desktop_thread_name` and native `provider_thread_id` before the worker brief.
- After `wait_for_worker` completes, and immediately before `REVIEW_REQUESTED` evidence is constructed, re-fetch the AO session `displayName` and the conversation `providerThreadId`.
- Require exact equality with the originally established title and provider id. Missing title/id or any drift must fail closed and must not emit verified review evidence.
- Add focused regression coverage for successful post-worker re-verification and for title/provider-id absence or drift.

Modify only `scripts/aictrl_task_dispatch.py` and `tests/test_task_dispatch.py`. Preserve every other PRE-005 behavior, model policy, scope gate, tests, Defender gate, session cleanup, no Sol, no Goal mode, no auto-merge. Run targeted tests as useful; the controller bridge will run the canonical focused and full suites after your commit.

Commit and push to `{r1.BRANCH}`. Keep PR #35 open/non-draft. Do not merge and do not start follow-on work.

Your final assistant/provider message must be exactly this single line and nothing else:
{r1.READY_MARKER}
"""


def write_result(path, status, reason, session_id="", worker_head="", pr=None):
    lines = [
        "AICTRL_PRE005_R1_RESULT_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        f"repo: {r1.base.REPOSITORY}",
        "task_id: PRE-005",
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
r1.write_result = write_result


if __name__ == "__main__":
    raise SystemExit(r1.main())
