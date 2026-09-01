from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


EXPECTED_REPOSITORY = "qq230849622-a11y/AI-Dev-Control-Plane"
EXPECTED_ACTOR = "qq230849622-a11y"
EXPECTED_ISSUE_NUMBER = 7
EXPECTED_PROJECT_KEY = "AI_DEV_CONTROL_PLANE"
EXPECTED_MODEL = "gpt-5.6-luna"
EXPECTED_HEADER = "AICTRL_PRE003_PING_V1"
EXPECTED_PONG_AUTHOR = "github-actions[bot]"


@dataclass(frozen=True)
class AdmissionResult:
    admitted: bool
    event_id: str | None = None
    reason: str | None = None


def _binding_matches(event):
    if not isinstance(event, dict):
        return False
    repository = event.get("repository")
    sender = event.get("sender")
    issue = event.get("issue")
    comment = event.get("comment")
    if not all(isinstance(value, dict) for value in (repository, sender, issue, comment)):
        return False
    return (
        event.get("action") == "created"
        and repository.get("full_name") == EXPECTED_REPOSITORY
        and sender.get("login") == EXPECTED_ACTOR
        and issue.get("number") == EXPECTED_ISSUE_NUMBER
    )


def _parse_ping(body):
    if not isinstance(body, str):
        return None

    lines = body.splitlines()
    if lines.count(EXPECTED_HEADER) != 1 or len(lines) != 5:
        return None
    if lines[:3] != [
        EXPECTED_HEADER,
        f"project_key: {EXPECTED_PROJECT_KEY}",
        f"repo: {EXPECTED_REPOSITORY}",
    ]:
        return None
    if not lines[3].startswith("event_id: ") or lines[4] != f"model: {EXPECTED_MODEL}":
        return None

    event_id = lines[3].removeprefix("event_id: ").strip()
    return event_id or None


def admit_issue_comment(event):
    if not _binding_matches(event):
        return AdmissionResult(admitted=False, reason="EVENT_BINDING_MISMATCH")

    event_id = _parse_ping(event["comment"].get("body"))
    if event_id is None:
        return AdmissionResult(admitted=False, reason="INVALID_ENVELOPE")

    return AdmissionResult(admitted=True, event_id=event_id)


def has_matching_pong(comment_bodies, event_id):
    expected_prefix = [
        "AICTRL_PRE003_PONG_V1",
        f"project_key: {EXPECTED_PROJECT_KEY}",
        f"repo: {EXPECTED_REPOSITORY}",
        f"event_id: {event_id}",
        "runner: AICTRL-WIN11",
        f"model: {EXPECTED_MODEL}",
    ]
    for comment in comment_bodies:
        if not isinstance(comment, dict):
            continue
        user = comment.get("user")
        if not isinstance(user, dict) or user.get("login") != EXPECTED_PONG_AUTHOR:
            continue
        body = comment.get("body")
        if not isinstance(body, str):
            continue
        lines = body.splitlines()
        if lines.count("AICTRL_PRE003_PONG_V1") != 1 or len(lines) != 13:
            continue
        if lines[:6] != expected_prefix:
            continue
        if not lines[6].startswith("session_id: ") or not lines[6][12:].strip():
            continue
        if lines[7] != f"result_marker: {build_probe_marker(event_id)}":
            continue
        if not lines[8].startswith("main_head: ") or len(lines[8][11:].strip()) != 40:
            continue
        if not lines[10].startswith("worktree_path: ") or not lines[10][15:].strip():
            continue
        if lines[9:] == ["main_unchanged: true", lines[10], "worktree_isolated: true", "status: PASS"]:
            return True
    return False


def build_probe_marker(event_id):
    digest = sha256(event_id.encode("utf-8")).hexdigest()[:16]
    return f"AICTRL_PRE003_MARKER_{digest}"


def is_luna_session(session_document):
    """Verify the AO session identity before model proof.

    The installed AO v0.12.10 runtime was observed to return an empty
    SessionView.model for a terminated Chat worker even though the bound
    conversation settings reported the explicitly selected Luna model.  The
    exact model is therefore proven on the conversation surface; this guard
    keeps the independent session-id + Codex-harness binding fail-closed.
    """
    if not isinstance(session_document, dict):
        return False
    session = session_document.get("session")
    return (
        isinstance(session, dict)
        and isinstance(session.get("id"), str)
        and bool(session["id"])
        and session.get("harness") == "codex"
    )


def has_exact_session_result(session_document, marker):
    if not isinstance(session_document, dict) or not isinstance(marker, str):
        return False
    messages = session_document.get("messages")
    return isinstance(messages, list) and any(
        isinstance(message, dict)
        and message.get("role") == "assistant"
        and message.get("origin") == "provider"
        and message.get("text") == marker
        for message in messages
    )
