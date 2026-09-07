# Execution Budget Policy V1

Deliver a complete outcome inside a bounded session, then verify at gates.
The existing AICTRL_TASK_V1 envelope is the goal contract; no second task queue,
status machine, provider Goal mode, or model substitution is introduced.

## Goal authoring and continuous work

The minimum useful goal is one independently reviewable outcome with explicit
acceptance criteria. Include the related implementation, regression tests,
documentation and necessary repairs in its allowed scope. A focused defect fix
can be a complete goal. Do not impose minimum lines, files, subtasks or minutes:
those are poor proxies for value. Existing objective/outcome/acceptance fields
remain required; judging whether they describe a coherent deliverable is an
author/reviewer responsibility, not a string-length heuristic.

The worker batches implementation and continues through ordinary relevant test
failures. A completed internal subtask is a checkpoint, not a new permission gate.
All work stays within the admitted project/repository, allowed and forbidden
scope, dependencies and explicit human gates. Related files outside that scope
still need a revised authorized contract. One worker session, one branch, one PR,
max_attempts=1 and the existing fixed model admission remain in force.

## Configuration and compatibility

An optional `execution_budget_policy` field enables the budgeted controller test
gate. Omission preserves the legacy testing gate and 1200-second worker budget.
All worker briefs now carry goal-oriented batching and targeted-check guidance.
For a new complete goal, use for example:

```json
"execution_budget_policy": {
  "version": 1,
  "worker_seconds": 3600,
  "self_repair_rounds": 3,
  "reuse_seconds": 300,
  "reusable_commands": []
}
```

Only version is required. Defaults are 1200 seconds, 3 repair rounds, 300 seconds
reuse lifetime and no reusable commands. Schema limits worker time to 60–3600
seconds, repair guidance to 1–10 rounds and reuse lifetime to 0–900 seconds.
Zero reuse lifetime disables reuse. The workflow has a 90-minute outer limit to
leave room for setup, up to 60 minutes of worker time, a 15-minute total test
budget, final identity checks, cleanup and evidence posting. This is not a token
or subscription quota limit, and no savings percentage is asserted.

`goal_mode=false` remains mandatory: that existing field controls the provider
feature, not goal-oriented task authoring. This change does not enable Astra or
alter the admitted Luna/Terra model matrix.

## Validation and pause rules

| Stage | Behavior |
| --- | --- |
| Implementation batch | Run targeted checks when they resolve an error or validate a meaningful affected path. |
| Ordinary failure | Diagnose and repair inside the same session; each focused round changes the hypothesis or input. |
| Repeated failure | Stop an unchanged/stalled loop; after the configured repair rounds report the unresolved failure under the existing result/escalation contract. |
| Shared infrastructure or explicit project requirement | Broaden validation when justified, even during implementation. |
| Ready for review | Controller independently executes canonical testing_policy commands with a total 900-second budget for opted-in goals. A failed final gate never becomes READY_FOR_REVIEW. |
| Final acceptance | The authorized reviewer checks coverage and current evidence; changed code/config/environment or explicit requirements demand fresh checks. This dispatcher does not merge or accept. |
| Boundary conflict | Stop on identity/scope/authority conflict, unmet dependency, destructive or production action, systemic failure, or explicit human gate. |

Repair rounds describe worker behavior within its single session, not additional
dispatch attempts. They are prompt guidance: the controller cannot count internal
reasoning rounds and does not claim to enforce that count. Worker elapsed time,
final tests, identity, scope, PR state and cleanup are controller-enforced. A
worker that fails to return a valid final result still times out fail-closed.
Normal controller API-call latency can extend the polling deadline before cleanup.

Do not mechanically run the full suite after each small edit, or require two full
suite runs regardless of unchanged evidence. Reserve the canonical suite for the
controller gate and re-run during acceptance only when needed. Required project
checks override this optimization. Optional tests retain CTRL-HARDEN-003 semantics:
required=false requires commands=[]; required=true requires nonempty commands.

## Duplicate suppression and evidence reuse

Reuse is conservative and opt-in. `reusable_commands` must be a subset of the
canonical testing commands and must contain only hermetic, deterministic checks
whose mutable inputs live in the workspace or environment. Checks depending on
network services, clocks/randomness, mutable external tools/configuration, Git
metadata beyond HEAD, or external files must remain non-reusable. The controller
cannot infer hermeticity from shell text; the trusted contract author declares it.
Metadata-dependent checks may rely on the modeled file/directory type, mode and
modification time. Checks relying on access/change times, inode identity, ACLs,
extended attributes or other unmodeled platform metadata must remain non-reusable.

The controller owns a private in-memory success ledger for one gate invocation.
It hashes the full task (including project/repo and policy), resolved workspace,
HEAD, environment, and all workspace file contents/types/modes/modification times,
including the root directory and ignored
dependencies. Git metadata is excluded. Hashing checks the gate deadline;
unreadable inputs, links/junctions or files changing while read disable reuse.
The actual environment values are never included in emitted evidence.

A duplicate command reuses only successful controller evidence within its age
limit when the before/after input fingerprints agree and current inputs still
match. Failures, changed inputs, expired evidence and undeclared commands run or
fail normally. Cache files and worker claims are never imported. Reuse does not
cross processes, dispatches, CI runs or reviewer acceptance. Broad hashing has a
cost, so leave the default empty list unless duplicate expensive hermetic checks
actually justify it. There is no automatic cross-commit documentation exception.

REVIEW_REQUESTED includes the effective budget and each canonical command's
PASSED/REUSED record with its fingerprint when available. These are test evidence,
not independent acceptance. After tests, controller rechecks main/worktree state,
remote branch, PR/head, scope and result binding; provider correlation and session
cleanup remain mandatory. The existing failure/cleanup override is preserved.

## Delivery boundary

PRE-006 still needs owner observation; CTRL-WAKE-001 still needs event-driven
ChatGPT Controller integration. Neither is solved by this policy. This change is
repository implementation with local verification, not a production worker probe,
automatic GPT review, merge, or human acceptance.
