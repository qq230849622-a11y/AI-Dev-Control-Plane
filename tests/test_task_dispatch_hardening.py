from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import scripts.aictrl_task_dispatch as base
import scripts.aictrl_task_dispatch_hardened as hard


BRANCH = "aictrl/pre-006"
TIP = "a" * 40


def result(returncode=0, stdout=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


@pytest.fixture(autouse=True)
def no_historical_pr_artifact(monkeypatch):
    monkeypatch.setattr(base, "github_json", lambda *args, **kwargs: [])


def test_absent_local_and_remote_branch_passes_without_mutation(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result()
        if command[1:3] == ["show-ref", "--exists"]:
            return result(2)
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    hard.reject_existing_artifacts(tmp_path, {"repo": "owner/repo"}, BRANCH)
    assert not any(command[1:3] == ["update-ref", "-d"] for command in calls)


def test_remote_branch_always_fails_closed_before_pr_or_local_reclamation(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(base, "github_json", lambda *args, **kwargs: pytest.fail("PR lookup must not run after remote artifact"))

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result(stdout=f"{TIP}\trefs/heads/{BRANCH}\n")
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    with pytest.raises(base.DispatchFailure, match="REMOTE_BRANCH_EXISTS"):
        hard.reject_existing_artifacts(tmp_path, {"repo": "owner/repo"}, BRANCH)
    assert not any("show-ref" in command for command in calls)


def test_historical_pr_artifact_blocks_even_when_remote_branch_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(base, "github_json", lambda *args, **kwargs: [{"number": 7}])

    def fake_run(command, **kwargs):
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result()
        pytest.fail(f"local branch must not be inspected after PR artifact: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    with pytest.raises(base.DispatchFailure, match="PR_ARTIFACT_EXISTS"):
        hard.reject_existing_artifacts(tmp_path, {"repo": "owner/repo"}, BRANCH)


def test_pr_precheck_must_return_a_list(monkeypatch, tmp_path):
    monkeypatch.setattr(base, "github_json", lambda *args, **kwargs: {"number": 7})
    monkeypatch.setattr(base, "run", lambda command, **kwargs: result() if command[1:4] == ["ls-remote", "--heads", "origin"] else pytest.fail(f"unexpected command: {command}"))
    with pytest.raises(base.DispatchFailure, match="PR_PRECHECK_FAILED"):
        hard.reject_existing_artifacts(tmp_path, {"repo": "owner/repo"}, BRANCH)


def test_worktree_bound_local_branch_is_preserved(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result()
        if command[1:3] == ["show-ref", "--exists"]:
            return result()
        if command[1:4] == ["show-ref", "--verify", "--hash"]:
            return result(stdout=TIP + "\n")
        if command[1:4] == ["worktree", "list", "--porcelain"]:
            return result(stdout=f"worktree C:/tmp/wt\nHEAD {TIP}\nbranch refs/heads/{BRANCH}\n")
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    with pytest.raises(base.DispatchFailure, match="LOCAL_BRANCH_WORKTREE_BOUND"):
        hard.reject_existing_artifacts(tmp_path, {"repo": "owner/repo"}, BRANCH)


def test_unmerged_local_branch_is_preserved(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result()
        if command[1:3] == ["show-ref", "--exists"]:
            return result()
        if command[1:4] == ["show-ref", "--verify", "--hash"]:
            return result(stdout=TIP + "\n")
        if command[1:4] == ["worktree", "list", "--porcelain"]:
            return result(stdout="worktree C:/repo\nHEAD cafe\nbranch refs/heads/master\n")
        if command[1:4] == ["merge-base", "--is-ancestor", TIP]:
            return result(1)
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    with pytest.raises(base.DispatchFailure, match="LOCAL_BRANCH_UNMERGED"):
        hard.reject_existing_artifacts(tmp_path, {"repo": "owner/repo"}, BRANCH)


def test_fully_merged_unbound_local_residue_is_deleted_atomically_and_verified(monkeypatch, tmp_path):
    calls = []
    exists_calls = 0

    def fake_run(command, **kwargs):
        nonlocal exists_calls
        calls.append(command)
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result()
        if command[1:3] == ["show-ref", "--exists"]:
            exists_calls += 1
            return result() if exists_calls == 1 else result(2)
        if command[1:4] == ["show-ref", "--verify", "--hash"]:
            return result(stdout=TIP + "\n")
        if command[1:4] == ["worktree", "list", "--porcelain"]:
            return result(stdout="worktree C:/repo\nHEAD cafe\nbranch refs/heads/master\n")
        if command[1:4] == ["merge-base", "--is-ancestor", TIP]:
            return result()
        if command[1:3] == ["update-ref", "-d"]:
            return result()
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    hard.reject_existing_artifacts(tmp_path, {"repo": "owner/repo"}, BRANCH)
    delete_commands = [command for command in calls if command[1:3] == ["update-ref", "-d"]]
    assert delete_commands == [["git", "update-ref", "-d", f"refs/heads/{BRANCH}", TIP]]
    assert all("-D" not in command for command in calls)
    assert exists_calls == 2


def test_real_git_reclaims_only_an_already_reachable_local_ref(monkeypatch, tmp_path):
    subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "aictrl@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "AICTRL Test"], cwd=tmp_path, check=True)
    (tmp_path / "marker.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "marker.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "branch", BRANCH], cwd=tmp_path, check=True)

    original_run = base.run

    def intercept_remote_only(command, **kwargs):
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result()
        return original_run(command, **kwargs)

    monkeypatch.setattr(base, "run", intercept_remote_only)
    hard.reject_existing_artifacts(tmp_path, {"repo": "owner/repo"}, BRANCH)
    assert subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{BRANCH}"],
        cwd=tmp_path,
        check=False,
    ).returncode == 1


def test_local_ref_existence_lookup_error_fails_closed(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result()
        if command[1:3] == ["show-ref", "--exists"]:
            return result(1)
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    with pytest.raises(base.DispatchFailure, match="LOCAL_BRANCH_CHECK_FAILED"):
        hard.reject_existing_artifacts(tmp_path, {"repo": "owner/repo"}, BRANCH)


def test_local_ref_resolution_error_fails_closed(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        if command[1:4] == ["ls-remote", "--heads", "origin"]:
            return result()
        if command[1:3] == ["show-ref", "--exists"]:
            return result()
        if command[1:4] == ["show-ref", "--verify", "--hash"]:
            return result(128)
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(base, "run", fake_run)
    with pytest.raises(base.DispatchFailure, match="LOCAL_BRANCH_CHECK_FAILED"):
        hard.reject_existing_artifacts(tmp_path, {"repo": "owner/repo"}, BRANCH)


def test_wrapper_installs_only_the_hardened_preflight():
    original = base.reject_existing_artifacts
    hard.install()
    try:
        assert base.reject_existing_artifacts is hard.reject_existing_artifacts
    finally:
        base.reject_existing_artifacts = original


def test_production_workflow_uses_hardened_entrypoint_and_canonical_base():
    workflow = (Path(__file__).parents[1] / ".github/workflows/aictrl-task-dispatch.yml").read_text(encoding="utf-8")
    assert "python -m scripts.aictrl_task_dispatch_hardened" in workflow
    assert "python scripts/aictrl_task_dispatch.py" in workflow
    assert "PYTHONDONTWRITEBYTECODE: '1'" in workflow
