"""Production dispatcher hardening for provably safe stale local branch residue.

This thin wrapper preserves the canonical dispatcher and replaces only its
existing-artifact preflight. A deterministic local task branch may be reclaimed
only when Git positively proves that the remote branch is absent, no worktree
owns the branch, and the branch tip is already reachable from the synced
current HEAD. Ambiguity preserves data and fails closed.
"""

import scripts.aictrl_task_dispatch as base


def _local_branch_exists(main_path, branch):
    result = base.run(
        ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
        cwd=main_path,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise base.DispatchFailure("LOCAL_BRANCH_CHECK_FAILED")


def _branch_is_worktree_bound(main_path, branch):
    output = base.git(
        main_path,
        "worktree",
        "list",
        "--porcelain",
        code="WORKTREE_LIST_FAILED",
    )
    return f"branch refs/heads/{branch}" in output.splitlines()


def reject_existing_artifacts(main_path, task, branch):
    """Reject live artifacts; reclaim only a provably safe stale local branch."""
    remote = base.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=main_path,
    )
    if remote.returncode != 0:
        raise base.DispatchFailure("REMOTE_BRANCH_CHECK_FAILED")
    if remote.stdout.strip():
        raise base.DispatchFailure("REMOTE_BRANCH_EXISTS")

    if not _local_branch_exists(main_path, branch):
        return

    if _branch_is_worktree_bound(main_path, branch):
        raise base.DispatchFailure("LOCAL_BRANCH_WORKTREE_BOUND")

    ancestry = base.run(
        ["git", "merge-base", "--is-ancestor", f"refs/heads/{branch}", "HEAD"],
        cwd=main_path,
    )
    if ancestry.returncode == 1:
        raise base.DispatchFailure("LOCAL_BRANCH_UNMERGED")
    if ancestry.returncode != 0:
        raise base.DispatchFailure("LOCAL_BRANCH_ANCESTRY_CHECK_FAILED")

    deleted = base.run(["git", "branch", "--delete", branch], cwd=main_path)
    if deleted.returncode != 0:
        raise base.DispatchFailure("LOCAL_BRANCH_RECLAIM_FAILED")
    if _local_branch_exists(main_path, branch):
        raise base.DispatchFailure("LOCAL_BRANCH_RECLAIM_UNVERIFIED")


base.reject_existing_artifacts = reject_existing_artifacts


def main(argv=None):
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
