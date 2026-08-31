import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jsonschema import Draft202012Validator

from .validator import SCHEMA_DIRECTORY, validate_document


PROJECT_SCHEMA_PATH = SCHEMA_DIRECTORY / "project.schema.json"


@dataclass(frozen=True)
class ProjectBinding:
    project_key: str
    repo: str
    default_branch: str
    controller_key: str
    enabled: bool


class RegistryError(Exception):
    def __init__(self, error_code, detail):
        self.error_code = error_code
        self.detail = detail
        super().__init__(self.message)

    @property
    def message(self):
        return f"{self.error_code}: {self.detail}"


@dataclass(frozen=True)
class RouteResult:
    valid: bool
    project_key: Optional[str] = None
    repo: Optional[str] = None
    error_code: Optional[str] = None
    detail: Optional[str] = None

    @property
    def message(self):
        if self.valid:
            return f"ROUTED: {self.project_key} {self.repo}"
        return f"{self.error_code}: {self.detail}"


def _binding_paths(registry_path):
    path = Path(registry_path)
    if path.is_file():
        return [path]
    if path.is_dir():
        paths = sorted(path.glob("*.json"))
        if paths:
            return paths
        raise RegistryError("REGISTRY_EMPTY", str(path))
    raise RegistryError("REGISTRY_PATH_ERROR", str(path))


def _read_binding(path, validator):
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise RegistryError("REGISTRY_DOCUMENT_ERROR", path.name)

    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: (".".join(str(part) for part in error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise RegistryError("REGISTRY_SCHEMA_VIOLATION", f"{path.name} {location}")

    return ProjectBinding(
        project_key=document["project_key"],
        repo=document["repo"],
        default_branch=document["default_branch"],
        controller_key=document["controller_key"],
        enabled=document["enabled"],
    )


def load_registry(registry_path):
    try:
        schema = json.loads(PROJECT_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise RegistryError("REGISTRY_SCHEMA_ERROR", "project.schema.json")

    validator = Draft202012Validator(schema)
    bindings = []
    project_keys = set()
    enabled_repos = set()
    for path in _binding_paths(registry_path):
        binding = _read_binding(path, validator)
        if binding.project_key in project_keys:
            raise RegistryError("DUPLICATE_PROJECT_KEY", binding.project_key)
        if binding.enabled and binding.repo in enabled_repos:
            raise RegistryError("DUPLICATE_ENABLED_REPO", binding.repo)
        project_keys.add(binding.project_key)
        if binding.enabled:
            enabled_repos.add(binding.repo)
        bindings.append(binding)
    return tuple(bindings)


def route_path(registry_path, envelope_path):
    try:
        envelope = json.loads(Path(envelope_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return RouteResult(
            valid=False,
            error_code="ENVELOPE_INVALID",
            detail="INVALID_JSON",
        )
    except (OSError, UnicodeDecodeError):
        return RouteResult(
            valid=False,
            error_code="ENVELOPE_INVALID",
            detail="FILE_ERROR",
        )

    validation = validate_document(envelope)
    if not validation.valid:
        return RouteResult(
            valid=False,
            error_code="ENVELOPE_INVALID",
            detail=validation.error_code,
        )

    try:
        bindings = load_registry(registry_path)
    except RegistryError as error:
        return RouteResult(valid=False, error_code=error.error_code, detail=error.detail)

    project_key = envelope["project_key"]
    for binding in bindings:
        if binding.project_key == project_key:
            if not binding.enabled:
                return RouteResult(
                    valid=False,
                    error_code="PROJECT_DISABLED",
                    detail=project_key,
                )
            if binding.repo != envelope["repo"]:
                return RouteResult(
                    valid=False,
                    error_code="PROJECT_REPO_MISMATCH",
                    detail=project_key,
                )
            return RouteResult(valid=True, project_key=project_key, repo=binding.repo)

    return RouteResult(
        valid=False,
        error_code="PROJECT_NOT_REGISTERED",
        detail=project_key,
    )
