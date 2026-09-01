import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from aictrl.cli import main


HEAD_SHA = "e2663570e48dcdaf85ec7a9e9d7ed703e14c88cc"


def task_document():
    return {
        "protocol": "AICTRL_TASK_V1",
        "project_key": "AI_DEV_CONTROL_PLANE",
        "repo": "qq230849622-a11y/AI-Dev-Control-Plane",
        "task_id": "CTRL-002",
        "head_sha": HEAD_SHA,
        "objective": "Validate protocol envelopes locally.",
        "outcome": "A strict local validator is available.",
        "dependencies": ["CTRL-001"],
        "allowed_scope": ["schemas/**", "src/aictrl/**", "tests/**"],
        "forbidden_scope": ["CTRL-003"],
        "acceptance_criteria": ["All protocol schemas validate."],
        "complexity": "C2",
        "model": "terra",
        "reasoning": "medium",
        "goal_mode": False,
        "max_attempts": 2,
        "testing_policy": {"required": True, "commands": ["python -m pytest -q"]},
        "escalation_policy": {"on_failure": "STOP", "on_blocker": "ESCALATE"},
        "owner_gate_required": True,
        "status": "READY",
        "owner": "qq230849622-a11y",
    }


def event_document():
    return {
        "protocol": "AICTRL_EVENT_V1",
        "project_key": "AI_DEV_CONTROL_PLANE",
        "repo": "qq230849622-a11y/AI-Dev-Control-Plane",
        "task_id": "CTRL-002",
        "head_sha": HEAD_SHA,
        "event_id": "event-001",
        "actor": "codex",
        "event_type": "TASK_STARTED",
        "status": "RECORDED",
        "occurred_at": "2026-09-01T00:00:00Z",
        "payload": {"detail": "validator work started"},
    }


def decision_document():
    return {
        "protocol": "AICTRL_DECISION_V1",
        "project_key": "AI_DEV_CONTROL_PLANE",
        "repo": "qq230849622-a11y/AI-Dev-Control-Plane",
        "task_id": "CTRL-002",
        "head_sha": HEAD_SHA,
        "decision_id": "decision-001",
        "actor": "codex",
        "decision_type": "IMPLEMENTATION_SCOPE",
        "status": "APPROVED",
        "decision": "Implement schemas and local validation only.",
        "rationale": "Required by CTRL-002.",
    }


def result_document():
    return {
        "protocol": "AICTRL_RESULT_V1",
        "project_key": "AI_DEV_CONTROL_PLANE",
        "repo": "qq230849622-a11y/AI-Dev-Control-Plane",
        "task_id": "CTRL-002",
        "head_sha": HEAD_SHA,
        "result_id": "result-001",
        "actor": "codex",
        "status": "READY_FOR_REVIEW",
        "progress_delta": ["CODE_DELTA", "TEST_DELTA"],
        "summary": "Protocol validation is ready for review.",
        "evidence": ["python -m pytest -q"],
    }


def write_document(tmp_path, document, name="document.json"):
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def run_module_validation(path):
    project_root = Path(__file__).parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")
    return subprocess.run(
        [sys.executable, "-m", "aictrl", "validate", str(path)],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "document_factory, protocol",
    [
        (task_document, "AICTRL_TASK_V1"),
        (event_document, "AICTRL_EVENT_V1"),
        (decision_document, "AICTRL_DECISION_V1"),
        (result_document, "AICTRL_RESULT_V1"),
    ],
)
def test_module_cli_accepts_each_valid_protocol(tmp_path, document_factory, protocol):
    path = write_document(tmp_path, document_factory())

    result = run_module_validation(path)

    assert result.returncode == 0
    assert result.stdout == f"VALID: {protocol}\n"
    assert result.stderr == ""


def test_entrypoint_cli_accepts_valid_task(tmp_path, capsys):
    path = write_document(tmp_path, task_document())

    assert main(["validate", str(path)]) == 0
    assert capsys.readouterr().out == "VALID: AICTRL_TASK_V1\n"


def test_installed_console_cli_accepts_valid_task(tmp_path):
    project_root = Path(__file__).parents[1]
    project_copy = tmp_path / "project-copy"
    shutil.copytree(
        project_root,
        project_copy,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", "*.egg-info", "build"),
    )
    virtual_environment = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(virtual_environment)],
        check=True,
        capture_output=True,
        text=True,
    )
    scripts_directory = virtual_environment / ("Scripts" if os.name == "nt" else "bin")
    virtual_python = scripts_directory / ("python.exe" if os.name == "nt" else "python")
    installation = subprocess.run(
        [str(virtual_python), "-m", "pip", "install", "--no-deps", "."],
        cwd=project_copy,
        capture_output=True,
        text=True,
    )

    assert installation.returncode == 0, installation.stderr

    result = subprocess.run(
        [str(scripts_directory / ("aictrl.exe" if os.name == "nt" else "aictrl")), "validate", str(write_document(tmp_path, task_document()))],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == "VALID: AICTRL_TASK_V1\n"
    assert result.stderr == ""


def test_unknown_protocol_is_rejected(tmp_path):
    document = task_document()
    document["protocol"] = "AICTRL_UNKNOWN_V1"

    result = run_module_validation(write_document(tmp_path, document))

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "UNKNOWN_PROTOCOL: AICTRL_UNKNOWN_V1\n"


def test_non_string_protocol_is_rejected(tmp_path):
    document = task_document()
    document["protocol"] = []

    result = run_module_validation(write_document(tmp_path, document))

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "UNKNOWN_PROTOCOL: <missing>\n"


def test_malformed_json_is_rejected(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    result = run_module_validation(path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("INVALID_JSON: ")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.pop("project_key"),
        lambda document: document.__setitem__("repo", "not-an-owner-name"),
        lambda document: document.__setitem__("head_sha", "ABC123"),
        lambda document: document.__setitem__("head_sha", f"{HEAD_SHA}\n"),
        lambda document: document.__setitem__("unexpected", "field"),
        lambda document: document.__setitem__("model", "unsupported"),
    ],
)
def test_task_schema_violations_are_rejected(tmp_path, mutation):
    document = task_document()
    mutation(document)

    result = run_module_validation(write_document(tmp_path, document))

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("SCHEMA_VIOLATION: ")


def test_result_rejects_none_with_another_progress_delta(tmp_path):
    document = result_document()
    document["progress_delta"] = ["NONE", "CODE_DELTA"]

    result = run_module_validation(write_document(tmp_path, document))

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("SCHEMA_VIOLATION: ")
