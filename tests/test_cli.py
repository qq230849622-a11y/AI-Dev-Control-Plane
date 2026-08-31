import os
import subprocess
import sys
from pathlib import Path

import pytest

from aictrl.cli import main


def test_module_version_command():
    project_root = Path(__file__).parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")

    result = subprocess.run(
        [sys.executable, "-m", "aictrl", "--version"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "aictrl 0.1.0"


def test_entrypoint_version_command(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == "aictrl 0.1.0"
