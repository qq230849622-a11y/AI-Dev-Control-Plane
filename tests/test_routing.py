import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from aictrl.cli import main
from aictrl.registry import route_path
from test_validator import (
    decision_document,
    event_document,
    result_document,
    task_document,
)


PROJECT_KEY = "AI_DEV_CONTROL_PLANE"
REPO = "qq230849622-a11y/AI-Dev-Control-Plane"


def project_binding(**overrides):
    binding = {
        "protocol": "AICTRL_PROJECT_V1",
        "project_key": PROJECT_KEY,
        "repo": REPO,
        "default_branch": "master",
        "controller_key": "AI_DEV_CONTROL_PLANE_CONTROLLER",
        "enabled": True,
    }
    binding.update(overrides)
    return binding


def write_json(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def write_registry(tmp_path, *bindings):
    registry = tmp_path / "registry"
    registry.mkdir()
    for index, binding in enumerate(bindings):
        write_json(registry / f"project-{index}.json", binding)
    return registry


def run_module_route(registry, envelope):
    project_root = Path(__file__).parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")
    return subprocess.run(
        [sys.executable, "-m", "aictrl", "route", "--registry", str(registry), str(envelope)],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
    )


def test_valid_registered_project_routes_successfully(tmp_path):
    registry = write_registry(tmp_path, project_binding())
    envelope = write_json(tmp_path / "task.json", task_document())

    result = run_module_route(registry, envelope)

    assert result.returncode == 0
    assert result.stdout == f"ROUTED: {PROJECT_KEY} {REPO}\n"
    assert result.stderr == ""


def test_route_command_is_available_through_entrypoint(tmp_path, capsys):
    registry = write_registry(tmp_path, project_binding())
    envelope = write_json(tmp_path / "task.json", task_document())

    assert main(["route", "--registry", str(registry), str(envelope)]) == 0
    assert capsys.readouterr().out == f"ROUTED: {PROJECT_KEY} {REPO}\n"


@pytest.mark.parametrize(
    "envelope_factory",
    [task_document, event_document, decision_document, result_document],
)
def test_each_existing_envelope_protocol_uses_the_same_binding_rule(tmp_path, envelope_factory):
    registry = write_registry(tmp_path, project_binding())
    envelope = write_json(tmp_path / "envelope.json", envelope_factory())

    result = run_module_route(registry, envelope)

    assert result.returncode == 0
    assert result.stdout == f"ROUTED: {PROJECT_KEY} {REPO}\n"


@pytest.mark.parametrize(
    "envelope_change, error",
    [
        ({"repo": "qq230849622-a11y/wrong-repo"}, f"PROJECT_REPO_MISMATCH: {PROJECT_KEY}"),
        ({"project_key": "OTHER_PROJECT"}, "PROJECT_NOT_REGISTERED: OTHER_PROJECT"),
    ],
)
def test_project_identity_mismatch_is_rejected_without_fallback(tmp_path, envelope_change, error):
    registry = write_registry(tmp_path, project_binding())
    envelope_document = task_document()
    envelope_document.update(envelope_change)
    envelope = write_json(tmp_path / "task.json", envelope_document)

    result = run_module_route(registry, envelope)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"{error}\n"


def test_unknown_project_is_rejected(tmp_path):
    registry = write_registry(tmp_path, project_binding())
    envelope_document = task_document()
    envelope_document["project_key"] = "UNKNOWN_PROJECT"
    envelope = write_json(tmp_path / "task.json", envelope_document)

    result = run_module_route(registry, envelope)

    assert result.returncode == 1
    assert result.stderr == "PROJECT_NOT_REGISTERED: UNKNOWN_PROJECT\n"


def test_disabled_project_is_rejected(tmp_path):
    registry = write_registry(tmp_path, project_binding(enabled=False))
    envelope = write_json(tmp_path / "task.json", task_document())

    result = run_module_route(registry, envelope)

    assert result.returncode == 1
    assert result.stderr == f"PROJECT_DISABLED: {PROJECT_KEY}\n"


@pytest.mark.parametrize(
    "bindings, error",
    [
        (
            (project_binding(), project_binding(repo="qq230849622-a11y/another-repo")),
            f"DUPLICATE_PROJECT_KEY: {PROJECT_KEY}",
        ),
        (
            (project_binding(), project_binding(project_key="OTHER_PROJECT")),
            f"DUPLICATE_ENABLED_REPO: {REPO}",
        ),
    ],
)
def test_registry_duplicate_machine_identities_are_invalid(tmp_path, bindings, error):
    registry = write_registry(tmp_path, *bindings)
    envelope = write_json(tmp_path / "task.json", task_document())

    result = run_module_route(registry, envelope)

    assert result.returncode == 1
    assert result.stderr == f"{error}\n"


@pytest.mark.parametrize(
    "binding",
    [
        {},
        project_binding(controller_key="display name"),
        project_binding(default_branch=""),
    ],
)
def test_malformed_project_binding_is_rejected(tmp_path, binding):
    registry = write_registry(tmp_path, binding)
    envelope = write_json(tmp_path / "task.json", task_document())

    result = run_module_route(registry, envelope)

    assert result.returncode == 1
    assert result.stderr.startswith("REGISTRY_SCHEMA_VIOLATION: ")


def test_non_utf8_project_binding_is_rejected_without_a_traceback(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "project-0.json").write_bytes(b"\xff")
    envelope = write_json(tmp_path / "task.json", task_document())

    result = run_module_route(registry, envelope)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "REGISTRY_DOCUMENT_ERROR: project-0.json\n"


def test_route_uses_the_same_envelope_document_that_it_validates(tmp_path, monkeypatch):
    registry = write_registry(tmp_path, project_binding())
    envelope = write_json(tmp_path / "task.json", task_document())
    original_read_text = Path.read_text
    envelope_reads = 0

    def read_text_once(path, *args, **kwargs):
        nonlocal envelope_reads
        if path == envelope:
            envelope_reads += 1
            if envelope_reads > 1:
                raise AssertionError("route must not reread the envelope")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text_once)

    result = route_path(registry, envelope)

    assert result.valid
    assert envelope_reads == 1


def test_invalid_envelope_is_rejected_before_routing(tmp_path):
    registry = write_registry(tmp_path, project_binding())
    envelope_document = task_document()
    del envelope_document["repo"]
    envelope = write_json(tmp_path / "task.json", envelope_document)

    result = run_module_route(registry, envelope)

    assert result.returncode == 1
    assert result.stderr == "ENVELOPE_INVALID: SCHEMA_VIOLATION\n"
