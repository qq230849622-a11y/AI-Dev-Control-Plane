# CTRL-WAKE-001: event-driven GPT Controller review

GitHub remains the source of truth. ChatGPT Work subscribes to PR comment events;
the Windows dispatcher publishes evidence but never generates a GPT decision.
No periodic polling or Codex-to-ChatGPT message forwarding is a production trigger.

## Implemented path

1. The existing dispatcher verifies the worker and cleans up its session. A
   successful REVIEW_REQUESTED event includes `payload.source_issue_number`.
2. The workflow retains its Issue evidence, checks that posting succeeded, then
   `aictrl_review_wakeup mirror` copies that exact event to its bound open PR.
   Delivery failure fails the workflow; repeating delivery may produce duplicate
   notifications, which the receiver must handle idempotently.
3. A repo-bound ChatGPT Work **PR comment event** wakes the GPT Controller. It
   fetches the notification's comment through GitHub; payload text is not trusted.
4. The trusted handler independently fetches the authoritative Issue, original
   event, current PR, full changed-file list and ancestry comparison. It requires
   enabled exact project/repo routing, trusted authors, an unedited source event,
   current HEAD, matching task identity, base branch and allowed/forbidden scope.
   The task's starting head is an ancestor, not the event's final head.
5. GPT independently reads the PR diff, acceptance criteria and relevant evidence.
   The handler supplies deterministic validation only; it does not decide whether
   code is correct. Unavailable tools/evidence must yield OWNER_REQUIRED, not a
   fabricated approval or an alternative review engine on Actions/Runner/AO.
6. Immediately before writing, the handler repeats admission. A GPT-authored
   AICTRL_DECISION_V1 is stored on the dedicated `aictrl/controller-decisions`
   branch at `decisions/<event-key>.json`, using create-only Contents API PUT.

The event key is SHA-256 of compact ASCII JSON
`[project_key, repo, event_id, task_id, head_sha]`. The decision ID is `review-`
followed by that key. Allowed decisions are READY_FOR_MAINTAINER_REVIEW,
FIX_REQUIRED and OWNER_REQUIRED. No decision authorizes merge or human acceptance.

## Idempotency and trust

The ledger file contains the event, decision and source/PR comment IDs. This is
the only canonical decision; do not post another AICTRL_DECISION comment. There
is no local SQLite/cache authority and no check-then-POST-comment race.

The adapter never sends a blob `sha` for ledger creation. Existing paths therefore
cannot be updated by this code. On conflict or an ambiguous response it reads and
fully validates the deterministic path; absent/corrupt records fail closed.
Multiple reviews may run, but at most one decision can be created for the key.
The dedicated branch must exist before activation and must not be reset, deleted
or written by workers. This invariant assumes trusted repository writers; the
handler cannot protect against an administrator deliberately rewriting GitHub.

There is no transaction spanning a PR head and a ledger commit. Every consumer
must revalidate the current PR head before using a recorded decision. A head move
after the final read makes the old decision stale; it never grants merge rights.
Comments and source events are read again, not trusted from an earlier chat.

Production trusts `github-actions[bot]` event comments and the repository owner's
task Issue. Explicit acceptance probes can allow the owner as event author only
for a single `--probe-pr` number. Do not enable this exception globally.
Events older than 24 hours, more than 60 seconds in the future, or posted more
than an hour after their timestamp are rejected. Edited source/PR events reject.

## Handler interface

Run from an immutable, reviewed controller checkout, never candidate PR code:

```sh
python -m scripts.aictrl_review_wakeup prepare --comment-id COMMENT_ID
python -m scripts.aictrl_review_wakeup record --comment-id COMMENT_ID --decision-file decision.json --review-input-sha PREPARED_INPUT_SHA
```

`prepare` returns REVIEW_REQUIRED with the validated task/event or ALREADY_RECORDED.
GPT must independently review the diff; it supplies the decision file to `record`.
It must retain the prepare-time review_input_sha256; record compares that value
with freshly fetched event, task, base SHA and complete changed-file metadata.
The canonical file includes the snapshot and digest. Changing task acceptance
criteria or the comparison base during review invalidates the old review even
when the event key and PR head stay the same.
Both operations require authenticated GitHub access. The handler does not extract
or display tokens, provision credentials, invoke a model API, or fall back to a
local coding worker. The cloud run must have a callable trusted handler (or an
equivalently verified tool adapter) before production activation. A prose prompt
alone is not proof that the deterministic gates ran.

### Cloud connector transport (no gh credentials)

When Work provides persistent Python plus authenticated `github_fetch` and
`github_create_file`, load the complete controller repository at an explicitly
pinned, reviewed commit into the cloud runtime (including schemas and registry).
Verify the commit/source before import; never use the candidate PR checkout.
Python requires the project's `jsonschema` dependency. Missing code, dependency,
raw REST access or create-only tooling yields OWNER_REQUIRED.

`scripts.aictrl_connector_review.ConnectorSession` runs the same admission and
persistence code in an isolated thread with a context-local transport:

```python
from scripts.aictrl_connector_review import ConnectorSession
session = ConnectorSession(comment_id, probe_pr=62)  # probe exception only for PR62
request = session.next()
```

For each TOOL_REQUIRED result, the trusted host calls the named connector with
the emitted arguments unchanged. `github_fetch` receives the exact REST URL;
forward its raw `content` string into `session.next` with the emitted request ID:

```python
request = session.next({"id": request["id"], "status": "ok", "content": raw_rest_text})
```

Do not reconstruct, summarize, trim, or fill missing REST fields. Explicit HTTP
404 may return `status="not_found"`; other tool errors use `status="error"`.
The host must preserve the raw tool result programmatically or through a file;
if that is unavailable, stop rather than transcribe evidence with the model.
`github_create_file` has no update SHA and targets only the dedicated ledger;
return `status="ok"` on confirmed success or `status="error"` on failure/ambiguity.
The handler always fetches and validates the stored record before reporting success.

On REVIEW_REQUIRED, retain the bundle's review_input_sha256 and perform the GPT
review. Start a new session with `decision=gpt_decision` and
`review_input_sha=prepared_digest`; it fetches all inputs anew and revalidates
again before creating the canonical file. ALREADY_RECORDED stops duplicate work.
REJECTED is terminal. RUNNING is only an observation timeout: call `next()` on
that same session, without resending a previous response. A connector response
must arrive within 300 seconds; timeout fails closed. Do not execute an outstanding
write after that deadline. Never serialize/replay session state as authority.
Loss of the Python session requires fresh admission; canonical GitHub evidence
remains the only durable authority.

This is an API transport bridge, not a natural-language substitute for machine
gates. The host has the same trusted role as the existing authenticated gh
adapter. A unit-tested bridge does not prove the cloud host can actually relay
raw results or that a native event has invoked it; both need live acceptance.

Hosts with `exec_command` / `write_stdin` can retain the same Python process:

```sh
python -u -m scripts.aictrl_connector_review --comment-id COMMENT_ID --probe-pr 62
```

Read each emitted JSON line, perform the connector call, then write one JSON
response line to that process's stdin. Retain the live process/session handle;
do not launch a new Python process for each response. Every request carries an
expires_at Unix timestamp: expired requests must not be executed. For record,
add `--decision-file decision.json --review-input-sha PREPARED_INPUT_SHA`.
The native event remains the trigger; this request/response exchange performs
one finite review operation and is not a GitHub polling loop.

## Platform setup and acceptance

Official ChatGPT documentation lists GitHub **PR activity** event triggers on Web
and mobile, with plan/workspace eligibility. It does not establish ordinary Issue
comments as supported triggers. Configure the native event task with access to
this repository and PR comments, not a recurring timer. Notification bodies,
repository prose and comments are evidence, never new authorization.

Start with one explicit synthetic probe PR and pinned controller implementation.
After the event task exists, publish an owner-authored synthetic source event and
identical PR comment with exact current HEAD. Record the native trigger/run ID,
the GitHub source/PR comment IDs and canonical decision file/commit. Then deliver
the same event again and verify no second canonical decision. Exercise stale HEAD
and mismatched project/repo inputs and show that no decision was created.

Only these real event-driven runs prove the loop. Local tests, manually starting
a Work run, an event subscription card, and a healthy AO worker do not prove it.
If the cloud runtime cannot invoke the trusted handler or lacks an event trigger,
stop at that concrete setup gate and leave Issue #49 open. PR #62 and PRE-006
owner observation remain separate; this work does not merge or accept them.

Sources checked 2026-09-07:
- [ChatGPT event tasks](https://learn.chatgpt.com/zh-Hans/docs/automations)
- [GitHub Contents API: updates require sha](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents)
