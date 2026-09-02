"""Production dispatcher hardening for provably safe stale local branch residue.

This thin wrapper preserves the canonical dispatcher and replaces only its
existing-artifact preflight. A deterministic local task branch may be reclaimed
only when Git positively proves that the remote branch is absent, no worktree
owns the branch, and the branch tip is already reachable from the synced
current HEAD. Ambiguity preserves data and fails closed.
"""

import scripts.aictrl_task_dispatch as base


def _local_branch_tip(main_path, branch):
    ref = f"refs/heads/{branch}"
    exists = base.run(["git", "show-ref", "--exists", ref], cwd=main_path)
    if exists.returncode == 2:
        return None
    if exists.returncode != 0:
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
        ["git", "update-ref", "-d", f"refs/heads/{branch}", local_tip],
        cwd=main_path,
    )
    if deleted.returncode != 0:
        raise base.DispatchFailure("LOCAL_BRANCH_RECLAIM_FAILED")
    if _local_branch_tip(main_path, branch) is not None:
        raise base.DispatchFailure("LOCAL_BRANCH_RECLAIM_UNVERIFIED")


def install():
    base.reject_existing_artifacts = reject_existing_artifacts


def main(argv=None):
    install()
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
