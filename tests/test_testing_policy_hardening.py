from types import SimpleNamespace

import pytest

import scripts.aictrl_task_dispatch as base
import scripts.aictrl_task_dispatch_hardened as hard


def result(returncode=0):
    return SimpleNamespace(returncode=returncode, stdout="", stderr="")


def test_optional_testing_policy_with_no_commands_is_valid_and_runs_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(base, "run", lambda *args, **kwargs: pytest.fail("optional empty policy must not execute commands"))
    hard.run_testing_policy(tmp_path, {"testing_policy": {"required": False, "commands": []}})


def test_required_testing_policy_requires_at_least_one_command(tmp_path):
    with pytest.raises(base.DispatchFailure, match="TESTING_POLICY_INVALID"):
        hard.run_testing_policy(tmp_path, {"testing_policy": {"required": True, "commands": []}})


def test_optional_testing_policy_rejects_nonempty_commands_as_ambiguous(tmp_path):
    with pytest.raises(base.DispatchFailure, match="TESTING_POLICY_INVALID"):
        hard.run_testing_policy(tmp_path, {"testing_policy": {"required": False, "commands": ["python -V"]}})


def test_required_testing_policy_executes_each_command_exactly_once(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return result()

    monkeypatch.setattr(base, "run", fake_run)
    hard.run_testing_policy(
        tmp_path,
        {"testing_policy": {"required": True, "commands": ["python -V", "python -m pytest -q"]}},
    )
    assert calls == [
        ("python -V", {"cwd": tmp_path, "timeout": 900, "shell": True}),
        ("python -m pytest -q", {"cwd": tmp_path, "timeout": 900, "shell": True}),
    ]


def test_required_testing_policy_fails_on_nonzero_command(monkeypatch, tmp_path):
    monkeypatch.setattr(base, "run", lambda *args, **kwargs: result(1))
    with pytest.raises(base.DispatchFailure, match="CONTROLLER_TESTS_FAILED"):
        hard.run_testing_policy(
            tmp_path,
            {"testing_policy": {"required": True, "commands": ["python -m pytest -q"]}},
        )


@pytest.mark.parametrize(
    "policy",
    [
        None,
        {},
        {"required": 1, "commands": []},
        {"required": False, "commands": "python -V"},
        {"required": True, "commands": [""]},
    ],
)
def test_malformed_testing_policy_fails_closed(policy, tmp_path):
    with pytest.raises(base.DispatchFailure, match="TESTING_POLICY_INVALID"):
        hard.run_testing_policy(tmp_path, {"testing_policy": policy})


def test_install_overrides_testing_policy_and_preserves_stale_branch_hardening():
    original_testing = base.run_testing_policy
    original_artifacts = base.reject_existing_artifacts
    hard.install()
    try:
        assert base.run_testing_policy is hard.run_testing_policy
        assert base.reject_existing_artifacts is hard.reject_existing_artifacts
    finally:
        base.run_testing_policy = original_testing
        base.reject_existing_artifacts = original_artifacts
