# Contributing to AI Dev Control Plane

Thank you for helping improve AI Dev Control Plane. Contributions should keep the
control plane deterministic, auditable, and fail-closed.

## Before opening an issue or pull request

- Search existing issues and pull requests.
- For security-sensitive behavior, read [SECURITY.md](SECURITY.md) first.
- Keep changes focused and explain the user-visible or operator-visible outcome.
- Do not include credentials, private URLs, local machine paths, runner logs, or
  production identifiers in commits or issue comments.

## Development setup

Python 3.9 or newer is supported.

~~~sh
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pytest
python -m pytest -q
~~~

On Windows PowerShell, activate the environment with
`.venv\Scripts\Activate.ps1` and use the same install and test commands.

## Pull requests

Every pull request should include:

1. a concise problem statement;
2. the smallest coherent implementation;
3. tests for changed behavior and failure paths;
4. evidence that the complete test suite passes;
5. notes about compatibility, security, or migration impact.

Please do not merge changes that weaken explicit identity binding, validation,
scope checks, or fail-closed behavior.

## Commit style

Use short imperative commit subjects, for example:

~~~text
fix: reject stale head SHA in dispatcher
~~~

The project may request a signed-off-by line for some contributions in future;
follow the repository instructions shown on the pull request.
