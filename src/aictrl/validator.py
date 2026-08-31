import json
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jsonschema import Draft202012Validator


SCHEMA_FILENAMES = {
    "AICTRL_TASK_V1": "task.schema.json",
    "AICTRL_EVENT_V1": "event.schema.json",
    "AICTRL_DECISION_V1": "decision.schema.json",
    "AICTRL_RESULT_V1": "result.schema.json",
}


def _schema_directory():
    source_directory = Path(__file__).resolve().parents[2] / "schemas" / "v1"
    if source_directory.is_dir():
        return source_directory
    return Path(sysconfig.get_path("data")) / "ai_dev_control_plane" / "schemas" / "v1"


SCHEMA_DIRECTORY = _schema_directory()


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    protocol: Optional[str]
    error_code: Optional[str] = None
    detail: Optional[str] = None

    @property
    def message(self):
        if self.valid:
            return f"VALID: {self.protocol}"
        return f"{self.error_code}: {self.detail}"


def validate_path(path):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return ValidationResult(
            valid=False,
            protocol=None,
            error_code="INVALID_JSON",
            detail=f"line {error.lineno} column {error.colno}",
        )
    except OSError:
        return ValidationResult(
            valid=False,
            protocol=None,
            error_code="FILE_ERROR",
            detail=str(path),
        )

    if not isinstance(document, dict):
        return ValidationResult(
            valid=False,
            protocol=None,
            error_code="UNKNOWN_PROTOCOL",
            detail="<missing>",
        )

    protocol = document.get("protocol")
    if not isinstance(protocol, str) or protocol not in SCHEMA_FILENAMES:
        detail = protocol if isinstance(protocol, str) else "<missing>"
        return ValidationResult(
            valid=False,
            protocol=None,
            error_code="UNKNOWN_PROTOCOL",
            detail=detail,
        )

    schema = json.loads((SCHEMA_DIRECTORY / SCHEMA_FILENAMES[protocol]).read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda error: (".".join(str(part) for part in error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        return ValidationResult(
            valid=False,
            protocol=protocol,
            error_code="SCHEMA_VIOLATION",
            detail=f"{location}: {error.message}",
        )

    return ValidationResult(valid=True, protocol=protocol)
