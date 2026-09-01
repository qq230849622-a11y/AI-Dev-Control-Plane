"""One-shot PRE-005 cleanup for the terminated AO worktree that still owns the PR branch."""

import scripts.aictrl_prod001_worktree_cleanup as cleanup


cleanup.ISSUE_NUMBER = 29
cleanup.SESSION_ID = "ai-dev-control-plane-12"
cleanup.BRANCH = "aictrl/pre-005"
cleanup.EXPECTED_HEAD = "ed08ff33341d4d3f7a4d086dc65d79ae25848876"
cleanup.EXPECTED_COMMENT = "\n".join(
    [
        "AICTRL_PRE005_WORKTREE_CLEANUP_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        "repo: qq230849622-a11y/AI-Dev-Control-Plane",
        "task_id: PRE-005",
        f"session_id: {cleanup.SESSION_ID}",
        f"branch: {cleanup.BRANCH}",
        f"head_sha: {cleanup.EXPECTED_HEAD}",
    ]
)


if __name__ == "__main__":
    raise SystemExit(cleanup.main())
