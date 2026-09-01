# Architecture

AI Dev Control Plane is a GitHub-native safety and orchestration layer for
bounded coding-agent work. It is designed for maintainers who need repeatable
task dispatch, explicit project identity, and reviewable evidence.

## Control flow

~~~text
GitHub issue comment
        |
        v
event admission -> task envelope validation -> project registry routing
        |
        v
policy checks -> bounded worker session -> branch/PR and test verification
        |
        v
controller evidence comment -> maintainer review
~~~

The controller treats every external boundary as untrusted. A task is admitted
only when the event, author, project binding, repository, task status, model
policy, attempt limit, and owner-gate policy all match the declared contract.

## Core contracts

The `schemas/v1` directory defines five machine-readable envelope types:

- `AICTRL_TASK_V1`: an executable task with scope, acceptance criteria, model and
  testing policy;
- `AICTRL_EVENT_V1`: an immutable event record;
- `AICTRL_DECISION_V1`: an explicit review or control decision;
- `AICTRL_RESULT_V1`: bounded outcome and evidence reporting;
- `AICTRL_PROJECT_V1`: a repository binding used by the registry.

The `aictrl validate` command validates one envelope without network access.
The `aictrl route` command validates an envelope and requires an exact,
enabled project-key/repository match.

## Execution boundary

The production dispatcher is deliberately narrower than a general-purpose
agent runner. It:

- accepts controller-authored issue comments only;
- runs one task at a time with a fixed attempt limit;
- checks the repository and current head SHA before and after work;
- verifies the worker session, branch, changed-file scope, tests, and PR state;
- writes deterministic failure evidence when a gate is not satisfied.

The checked-in GitHub workflow uses a Windows self-hosted runner because the
current Agent Orchestrator integration is Windows-specific. The protocol and
validator remain usable on other operating systems.

## Repository layout

- `.ai-control/projects/` — explicit project registry bindings;
- `schemas/v1/` — JSON Schema contracts;
- `src/aictrl/` — validator, CLI, registry, and protocol helpers;
- `scripts/` — controller-side dispatch and verification scripts;
- `tests/` — unit and integration-style contract tests;
- `.github/workflows/` — public CI and bounded maintainer workflows.

## Design principles

1. Explicit identity beats inferred names.
2. Invalid, stale, or ambiguous state fails closed.
3. A result is not accepted without independently verifiable evidence.
4. One task, one branch, one bounded execution attempt.
5. External actions remain visible through GitHub review surfaces.
