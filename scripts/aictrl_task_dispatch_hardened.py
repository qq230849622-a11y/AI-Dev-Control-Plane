"""Production dispatcher hardening.

This thin wrapper preserves the canonical dispatcher while tightening two
production preflight/runtime boundaries:
1. provably safe reclamation of stale deterministic local task branches; and
2. exact execution semantics for the schema-valid optional testing policy.

Ambiguity preserves data and fails closed.
"""

import scripts.aictrl_task_dispatch as base


def _local_branch_tip(main_path, branch):
    ref = f"refs/heads/{branch}"
    exists = base.run(["git", "show-ref", "--exists", ref], cwd=main_path)
    if exists.returncode == 2:
        return None
    if exists.returncode != 0:
        raise base.DispatchFailure("LOCAL_BRANCH_CHECK_FAILED")

    symbolic = base.run(["git", "symbolic-ref", "-q", ref], cwd=main_path)
    if symbolic.returncode == 0:
        raise base.DispatchFailure("LOCAL_BRANCH_SYMBOLIC_REF")
    if symbolic.returncode != 1:
        raise base.DispatchFailure("LOCAL_BRANCH_CHECK_FAILED")

    resolved = base.run(["git", "show-ref", "--verify", "--hash", ref], cwd=main_path)
    if resolved.returncode != 0:
        raise base.DispatchFailure("LOCAL_BRANCH_CHECK_FAILED")
    tip = resolved.stdout.strip()
    if not tip:
        raise base.DispatchFailure("LOCAL_BRANCH_CHECK_FAILED")
    return tip


def _branch_is_worktree_bound(main_path, branch):
    output = base.git(
        main_path,
        "worktree",
        "list",
        "--porcelain",
        code="WORKTREE_LIST_FAILED",
    )
    return f"branch refs/heads/{branch}" in output.splitlines()


def _reject_pr_artifacts(task, branch):
    prs = base.github_json(
        [
            "pr",
            "list",
            "--repo",
            task["repo"],
            "--head",
            branch,
            "--state",
            "all",
            "--json",
            "number",
        ],
        "PR_PRECHECK_FAILED",
        target_repository=True,
    )
    if not isinstance(prs, list):
        raise base.DispatchFailure("PR_PRECHECK_FAILED")
    if prs:
        raise base.DispatchFailure("PR_ARTIFACT_EXISTS")


def reject_existing_artifacts(main_path, task, branch):
    """Reject live/history artifacts; reclaim only a provably safe stale local ref."""
    remote = base.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=main_path,
    )
    if remote.returncode != 0:
        raise base.DispatchFailure("REMOTE_BRANCH_CHECK_FAILED")
    if remote.stdout.strip():
        raise base.DispatchFailure("REMOTE_BRANCH_EXISTS")

    # Preserve the canonical all-state PR history boundary even when the remote
    # branch was deleted after a previous PR was closed or merged.
    _reject_pr_artifacts(task, branch)

    local_tip = _local_branch_tip(main_path, branch)
    if local_tip is None:
        return

    if _branch_is_worktree_bound(main_path, branch):
        raise base.DispatchFailure("LOCAL_BRANCH_WORKTREE_BOUND")

    ancestry = base.run(
        ["git", "merge-base", "--is-ancestor", local_tip, "HEAD"],
        cwd=main_path,
    )
    if ancestry.returncode == 1:
        raise base.DispatchFailure("LOCAL_BRANCH_UNMERGED")
    if ancestry.returncode != 0:
        raise base.DispatchFailure("LOCAL_BRANCH_ANCESTRY_CHECK_FAILED")

    deleted = base.run(
        ["git", "update-ref", "--no-deref", "-d", f"refs/heads/{branch}", local_tip],
        cwd=main_path,
    )
    if deleted.returncode != 0:
        raise base.DispatchFailure("LOCAL_BRANCH_RECLAIM_FAILED")
    if _local_branch_tip(main_path, branch) is not None:
        raise base.DispatchFailure("LOCAL_BRANCH_RECLAIM_UNVERIFIED")


def run_testing_policy(workspace, task):
    """Honor the V1 testing policy without inventing ambiguous semantics."""
    policy = task.get("testing_policy") if isinstance(task, dict) else None
    if not isinstance(policy, dict):
        raise base.DispatchFailure("TESTING_POLICY_INVALID")

    required = policy.get("required")
    commands = policy.get("commands")
    if type(required) is not bool or not isinstance(commands, list):
        raise base.DispatchFailure("TESTING_POLICY_INVALID")
    if any(not isinstance(command, str) or not command.strip() for command in commands):
        raise base.DispatchFailure("TESTING_POLICY_INVALID")

    if required is False:
        if commands:
            raise base.DispatchFailure("TESTING_POLICY_INVALID")
        return

    if not commands:
        raise base.DispatchFailure("TESTING_POLICY_INVALID")
    for command in commands:
        if base.run(command, cwd=workspace, timeout=900, shell=True).returncode != 0:
            raise base.DispatchFailure("CONTROLLER_TESTS_FAILED")


def install():
    base.reject_existing_artifacts = reject_existing_artifacts
    base.run_testing_policy = run_testing_policy


def main(argv=None):
    install()
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
