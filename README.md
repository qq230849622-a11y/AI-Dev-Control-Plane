# AI Dev Control Plane

AI Dev Control Plane is a GitHub-native, fail-closed control plane for bounded
coding-agent work. It turns maintainer-defined task envelopes into auditable
dispatches while keeping project identity, scope, tests, and review state
explicit.

The project is built for open-source maintainers who want to reduce the cost of
PR review, issue triage, release preparation, and other repetitive engineering
work without giving an autonomous worker an unbounded write surface.

## Why this project exists

Coding agents are useful at implementation, but a maintainer still needs to
control:

- which repository and project a task belongs to;
- which files and outcomes are allowed;
- which model and attempt budget may be used;
- whether the current branch and PR still match the task;
- what evidence is required before a result is accepted.

AICTRL makes those checks machine-readable and deterministic. Ambiguous identity,
invalid envelopes, stale heads, unexpected scope, failed tests, and missing
evidence are rejected instead of silently routed.

## Current capabilities

- JSON Schema validation for task, event, decision, result, and project-binding
  envelopes;
- exact `project_key` + `repo` routing with no default or fallback project;
- bounded GitHub issue-comment dispatch;
- fixed model/complexity policy and one-attempt execution boundaries;
- branch, PR, head-SHA, changed-file, test, and worker-session verification;
- deterministic failure evidence for blocked or incomplete work;
- Python CLI usable locally or in CI.

## Quick start

Requires Python 3.9+.

~~~sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[test]"

aictrl --version
aictrl validate path/to/envelope.json
aictrl route --registry .ai-control/projects path/to/envelope.json
python -m pytest -q

for file in examples/*.json; do
  aictrl validate "$file"
done
~~~

Validation is local and makes no network calls. The dispatcher integration is
optional and requires a separately configured GitHub token, a trusted
controller workflow, and the Windows self-hosted Agent Orchestrator runner
described in [Architecture](docs/ARCHITECTURE.md).

## Protocol contracts

Runnable sanitized examples are in [examples](examples). They are fixtures for
schema validation, not executable production tasks.

The canonical contracts live in [schemas/v1](schemas/v1). Every envelope
declares a protocol and explicit machine identity. Strict schemas reject unknown
fields where the contract defines a closed boundary.

The `AICTRL_RESULT_V1` contract uses explicit progress deltas, including
`NONE`, and does not permit `NONE` together with another delta.

## Security model

AICTRL is fail-closed by design. It does not infer a project from a display name,
fall back to a default repository, or accept a worker claim without independent
verification. Do not put credentials or private runtime logs in task envelopes,
issues, comments, or commits. See [SECURITY.md](SECURITY.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports and pull requests are
welcome when they include focused tests and reproducible evidence.

## License

Apache-2.0. See [LICENSE](LICENSE).