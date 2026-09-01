"""One-shot no-model PRE-005 R1 worktree/session diagnostic."""

import scripts.aictrl_prod001_r1_diag as diag


diag.ISSUE_NUMBER = 29
diag.BRANCH = "aictrl/pre-005"
diag.EXPECTED_COMMENT = "\n".join(
    [
        "AICTRL_PRE005_R1_DIAG_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        "repo: qq230849622-a11y/AI-Dev-Control-Plane",
        "task_id: PRE-005",
        "probe: worktree-state",
    ]
)


if __name__ == "__main__":
    raise SystemExit(diag.main())
