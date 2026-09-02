from types import SimpleNamespace

import pytest

import scripts.aictrl_task_dispatch as base
import scripts.aictrl_task_dispatch_hardened as hard


BRANCH = "aictrl/pre-006"


def result(returncode=0, stdout=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def test_absent_local_and_remote_branch_passes_without_mutation(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result()
        if command[1:3] == ["show-ref", "--verify"]:
            return result(1)
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    hard.reject_existing_artifacts(tmp_path, {}, BRANCH)
    assert not any(command[1:3] == ["branch", "--delete"] for command in calls)


def test_remote_branch_always_fails_closed_before_local_reclamation(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result(stdout=f"deadbeef\trefs/heads/{BRANCH}\n")
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    with pytest.raises(base.DispatchFailure, match="REMOTE_BRANCH_EXISTS"):
        hard.reject_existing_artifacts(tmp_path, {}, BRANCH)
    assert not any("show-ref" in command for command in calls)


def test_worktree_bound_local_branch_is_preserved(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result()
        if command[1:3] == ["show-ref", "--verify"]:
            return result()
        if command[1:4] == ["worktree", "list", "--porcelain"]:
            return result(stdout=f"worktree C:/tmp/wt\nHEAD deadbeef\nbranch refs/heads/{BRANCH}\n")
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    with pytest.raises(base.DispatchFailure, match="LOCAL_BRANCH_WORKTREE_BOUND"):
        hard.reject_existing_artifacts(tmp_path, {}, BRANCH)


def test_unmerged_local_branch_is_preserved(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result()
        if command[1:3] == ["show-ref", "--verify"]:
            return result()
        if command[1:4] == ["worktree", "list", "--porcelain"]:
            return result(stdout="worktree C:/repo\nHEAD cafe\nbranch refs/heads/master\n")
        if command[1:4] == ["merge-base", "--is-ancestor", f"refs/heads/{BRANCH}"]:
            return result(1)
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    with pytest.raises(base.DispatchFailure, match="LOCAL_BRANCH_UNMERGED"):
        hard.reject_existing_artifacts(tmp_path, {}, BRANCH)


def test_fully_merged_unbound_local_residue_is_deleted_non_force_and_verified(monkeypatch, tmp_path):
    calls = []
    show_ref_calls = 0

    def fake_run(command, **kwargs):
        nonlocal show_ref_calls
        calls.append(command)
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result()
        if command[1:3] == ["show-ref", "--verify"]:
            show_ref_calls += 1
            return result(0 if show_ref_calls == 1 else 1)
        if command[1:4] == ["worktree", "list", "--porcelain"]:
            return result(stdout="worktree C:/repo\nHEAD cafe\nbranch refs/heads/master\n")
        if command[1:4] == ["merge-base", "--is-ancestor", f"refs/heads/{BRANCH}"]:
            return result()
        if command[1:3] == ["branch", "--delete"]:
            return result()
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    hard.reject_existing_artifacts(tmp_path, {}, BRANCH)
    delete_commands = [command for command in calls if command[1:3] == ["branch", "--delete"]]
    assert delete_commands == [["git", "branch", "--delete", BRANCH]]
    assert all("-D" not in command for command in calls)
    assert show_ref_calls == 2


def test_wrapper_installs_only_the_hardened_preflight(monkeypatch):
    original = base.reject_existing_artifacts
    hard.install()
    try:
        assert base.reject_existing_artifacts is hard.reject_existing_artifacts
    finally:
        base.reject_existing_artifacts = original
