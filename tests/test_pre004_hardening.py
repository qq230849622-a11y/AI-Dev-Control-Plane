import json
from pathlib import Path

import pytest

import scripts.aictrl_pre004_dispatch as base
import scripts.aictrl_pre004_hardened_dispatch as hard


def test_distinct_worktree_requires_distinct_top_level_and_git_dir(monkeypatch, tmp_path):
    main = tmp_path / "main"
    work = tmp_path / "work"
    main.mkdir()
    work.mkdir()
    monkeypatch.setattr(base, "verify_isolated_workspace", lambda *_: None)
    monkeypatch.setattr(hard, "git_top_level", lambda path: Path(path).resolve())
    monkeypatch.setattr(hard, "git_dir", lambda path: Path(path).resolve() / ".git")
    monkeypatch.setattr(base, "git_common_dir", lambda _: tmp_path / "common")
    hard.verify_isolated_workspace(work, main)

    monkeypatch.setattr(hard, "git_top_level", lambda path: main.resolve())
    with pytest.raises(base.ProbeFailure, match="WORKTREE_ROOT_UNVERIFIED"):
        hard.verify_isolated_workspace(work, main)


def test_cleanup_must_confirm_terminated(monkeypatch):
    monkeypatch.setattr(base, "command_success", lambda *_: True)
    monkeypatch.setattr(
        base,
        "api_document",
        lambda *_: {"session": {"status": "terminated"}},
    )
    assert hard.cleanup_session_confirmed("ao", {"port": 3001}, "session-1") is True

    monkeypatch.setattr(base, "command_success", lambda *_: False)
    assert hard.cleanup_session_confirmed("ao", {"port": 3001}, "session-1") is False


def test_pr_all_states_requires_exactly_one_open_pr(monkeypatch, tmp_path):
    monkeypatch.setattr(
        base,
        "verify_pr_and_git",
        lambda *_: ({"number": 17, "url": "https://example/pr/17"}, "abc"),
    )
    good = json.dumps([
        {
            "number": 17,
            "url": "https://example/pr/17",
            "isDraft": False,
            "state": "OPEN",
            "headRefName": base.BRANCH,
            "baseRefName": "master",
        }
    ])

    class Result:
        returncode = 0
        stdout = good

    monkeypatch.setattr(base, "run", lambda *_, **__: Result())
    pr, head = hard.verify_pr_and_git(tmp_path, tmp_path, "main")
    assert pr["number"] == 17 and head == "abc"

    Result.stdout = json.dumps([json.loads(good)[0], {**json.loads(good)[0], "number": 99, "state": "CLOSED"}])
    with pytest.raises(base.ProbeFailure, match="PR_COUNT_ALL_STATES_MISMATCH"):
        hard.verify_pr_and_git(tmp_path, tmp_path, "main")


def test_workflow_initializes_result_before_checkout_and_uses_hardened_dispatcher():
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "aictrl-pre004.yml").read_text(encoding="utf-8")
    assert workflow.index("Initialize bounded failure result") < workflow.index("Check out trusted default branch")
    assert "python scripts/aictrl_pre004_hardened_dispatch.py" in workflow
