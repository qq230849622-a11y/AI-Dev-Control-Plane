"""Deterministic gates for event-driven ChatGPT review, not a GPT reviewer.

GitHub is the evidence store. Notifications are hints; callers fetch authoritative
objects independently. Only a controller-authored decision can be persisted.
"""

import fnmatch
import hashlib
import json
from datetime import datetime, timezone

from .validator import validate_document


class ReviewRejected(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise ReviewRejected(code)


def strict_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=unique,
                          parse_constant=lambda _: require(False, "INVALID_JSON"))
    except (ValueError, TypeError) as error:
        raise ReviewRejected("INVALID_JSON") from error


def utc(value):
    try:
        require(isinstance(value, str) and value.endswith("Z"), "INVALID_EVENT_TIME")
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ReviewRejected("INVALID_EVENT_TIME") from error


def event_key(event):
    fields = [event[k] for k in ("project_key", "repo", "event_id", "task_id", "head_sha")]
    return hashlib.sha256(json.dumps(fields, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def task_from_body(body):
    begin, end = "AICTRL_TASK_JSON_BEGIN", "AICTRL_TASK_JSON_END"
    require(body.count(begin) == body.count(end) == 1, "TASK_ENVELOPE_INVALID")
    task = strict_json(body.split(begin)[1].split(end)[0])
    require(validate_document(task).valid and task.get("protocol") == "AICTRL_TASK_V1", "TASK_INVALID")
    return task


def admit(binding, comment, source, task_issue, pr, changed_files, *, probe_pr=None, now=None):
    """Validate REST GitHub objects, not notification-provided claims.

    Production requires github-actions[bot]. Explicit owner probes are limited
    to one configured PR and never authorize merge or acceptance.
    """
    require(binding.enabled, "PROJECT_DISABLED")
    event = strict_json(comment.get("body", ""))
    require(validate_document(event).valid and event.get("protocol") == "AICTRL_EVENT_V1", "EVENT_INVALID")
    repo = binding.repo
    owner = repo.split("/")[0]
    require(event["project_key"] == binding.project_key and event["repo"] == repo, "PROJECT_REPO_MISMATCH")
    require(event["event_type"] == "REVIEW_REQUESTED" and event["status"] == "READY_FOR_REVIEW"
            and event["actor"] == "AICTRL_CONTROLLER", "EVENT_NOT_REVIEWABLE")
    payload = event["payload"]
    number = payload.get("pr_number")
    issue = payload.get("source_issue_number")
    require(type(number) is int and number > 0 and type(issue) is int and issue > 0, "EVENT_CONTEXT_MISSING")
    expected_pr = f"https://github.com/{repo}/pull/{number}"
    require(payload.get("pr_url") == expected_pr, "PR_URL_MISMATCH")
    authors = {"github-actions[bot]"}
    if probe_pr == number:
        authors.add(owner)
    for item, item_number in ((comment, number), (source, issue)):
        require(item.get("user", {}).get("login") in authors, "EVENT_AUTHOR_MISMATCH")
        require(item.get("issue_url") == f"https://api.github.com/repos/{repo}/issues/{item_number}", "COMMENT_REPO_MISMATCH")
        require(item.get("created_at") == item.get("updated_at"), "EDITED_EVENT")
        require(strict_json(item.get("body", "")) == event, "SOURCE_EVENT_MISMATCH")
    timestamp = utc(event["occurred_at"])
    now = now or datetime.now(timezone.utc)
    require(-60 <= (now - timestamp).total_seconds() <= 86400, "STALE_EVENT")
    for item in (comment, source):
        require(0 <= (utc(item["created_at"]) - timestamp).total_seconds() <= 3600, "EVENT_TIME_MISMATCH")
    require(task_issue.get("number") == issue and task_issue.get("user", {}).get("login") == owner,
            "TASK_SOURCE_MISMATCH")
    require(task_issue.get("url") == f"https://api.github.com/repos/{repo}/issues/{issue}", "TASK_SOURCE_MISMATCH")
    task = task_from_body(task_issue.get("body", ""))
    require(all(task[k] == event[k] for k in ("project_key", "repo", "task_id")), "TASK_BINDING_MISMATCH")
    require(task["owner"] == owner and task["status"] == "READY", "TASK_NOT_REVIEWABLE")
    require(pr.get("number") == number and pr.get("html_url") == expected_pr, "PR_BINDING_MISMATCH")
    require(pr.get("state") == "open" and pr.get("draft") is False and pr.get("merged_at") is None
            and pr.get("auto_merge") is None, "PR_NOT_REVIEWABLE")
    require(pr.get("base", {}).get("repo", {}).get("full_name") == repo
            and pr.get("head", {}).get("repo", {}).get("full_name") == repo, "PR_REPO_MISMATCH")
    require(pr["base"].get("ref") == binding.default_branch and pr["head"].get("sha") == event["head_sha"], "STALE_HEAD")
    require(isinstance(changed_files, list) and changed_files, "CHANGESET_MISSING")
    for item in changed_files:
        paths = [item.get("filename")]
        if item.get("status") == "renamed":
            paths.append(item.get("previous_filename"))
        for path in paths:
            require(isinstance(path, str) and path and not path.startswith("/") and ".." not in path.split("/"), "INVALID_PATH")
            require(any(fnmatch.fnmatchcase(path, p) for p in task["allowed_scope"])
                    and not any(fnmatch.fnmatchcase(path, p) for p in task["forbidden_scope"]), "SCOPE_MISMATCH")
    base_sha = pr["base"].get("sha")
    require(isinstance(base_sha, str) and len(base_sha) == 40
            and all(c in "0123456789abcdef" for c in base_sha), "PR_BASE_SHA_MISSING")
    review_input = {"event": event, "task": task, "base_sha": base_sha, "files": changed_files}
    input_sha = hashlib.sha256(json.dumps(review_input, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"event": event, "task": task, "key": event_key(event), "review_input": review_input,
            "review_input_sha256": input_sha, "comment_id": comment["id"], "source_comment_id": source["id"]}


def validate_decision(bundle, decision):
    event = bundle["event"]
    require(validate_document(decision).valid and decision.get("protocol") == "AICTRL_DECISION_V1", "DECISION_INVALID")
    require(all(decision[k] == event[k] for k in ("project_key", "repo", "task_id", "head_sha")), "DECISION_BINDING_MISMATCH")
    require(decision["decision_id"] == "review-" + bundle["key"]
            and decision["actor"] == "GPT_CONTROLLER" and decision["decision_type"] == "PR_REVIEW"
            and decision["status"] == "RECORDED", "DECISION_IDENTITY_MISMATCH")
    require(decision["decision"] in {"READY_FOR_MAINTAINER_REVIEW", "FIX_REQUIRED", "OWNER_REQUIRED"}, "DECISION_AUTHORITY_EXCEEDED")
    require(bool(decision["rationale"].strip()), "DECISION_RATIONALE_MISSING")


def decision_record(bundle, decision):
    validate_decision(bundle, decision)
    return {"event": bundle["event"], "decision": decision,
            "review_input": bundle["review_input"], "review_input_sha256": bundle["review_input_sha256"],
            "source_comment_id": bundle["source_comment_id"], "comment_id": bundle["comment_id"]}


def verify_record(bundle, record):
    require(isinstance(record, dict) and record.get("event") == bundle["event"], "LEDGER_EVENT_MISMATCH")
    require(record.get("review_input") == bundle["review_input"]
            and record.get("review_input_sha256") == bundle["review_input_sha256"], "LEDGER_INPUT_MISMATCH")
    validate_decision(bundle, record.get("decision"))
    require(type(record.get("source_comment_id")) is int and type(record.get("comment_id")) is int, "LEDGER_PROVENANCE_MISSING")
    return record


def persist_decision(bundle, decision, store, revalidate, reviewed_input_sha):
    """Store has read(path) and create(path, record); create MUST never update.

    No second canonical comment is posted. After ambiguous create, only a fully
    validated existing record proves completion; absence remains an error.
    """
    path = f"decisions/{bundle['key']}.json"
    require(reviewed_input_sha == bundle["review_input_sha256"], "REVIEW_INPUT_CHANGED")
    record = decision_record(bundle, decision)
    revalidate()
    existing = store.read(path)
    if existing is not None:
        return verify_record(bundle, existing), False
    try:
        store.create(path, record)
    except Exception:
        existing = store.read(path)
        require(existing is not None, "DECISION_WRITE_UNCONFIRMED")
        return verify_record(bundle, existing), False
    existing = store.read(path)
    require(existing is not None, "DECISION_WRITE_UNCONFIRMED")
    return verify_record(bundle, existing), True
