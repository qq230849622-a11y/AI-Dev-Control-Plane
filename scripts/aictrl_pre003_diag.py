import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen


AO_PROJECT_ID = "ai-dev-control-plane"
EXPECTED_REPOSITORY = "qq230849622-a11y/AI-Dev-Control-Plane"
EXPECTED_HEADER = "AICTRL_PRE003_DIAG_V1"


def ao_binary():
    configured = os.environ.get("AO_BIN")
    if configured:
        return Path(configured)
    found = shutil.which("ao")
    if found:
        return Path(found)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Programs" / "agent-orchestrator" / "resources" / "daemon" / "ao.exe"
    return Path()


def command_json(binary, *arguments):
    try:
        result = subprocess.run(
            [str(binary), *arguments], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def api_document(status, path):
    port = status.get("port") if isinstance(status, dict) else None
    if not isinstance(port, int):
        return None
    try:
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def marker_for(event_id):
    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:16]
    return f"AICTRL_PRE003_MARKER_{digest}"


def display_name_for(event_id):
    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:16]
    return f"pre003-{digest[:12]}"


def admitted_diag_event(path, target_event_id):
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected = "\n".join([EXPECTED_HEADER, f"target_event_id: {target_event_id}"])
    return (
        isinstance(event, dict)
        and event.get("action") == "created"
        and event.get("repository", {}).get("full_name") == EXPECTED_REPOSITORY
        and event.get("sender", {}).get("login") == "qq230849622-a11y"
        and event.get("issue", {}).get("number") == 7
        and event.get("comment", {}).get("body") == expected
    )


def diagnostic(target_event_id):
    binary = ao_binary()
    if not binary.is_file():
        return {"error": "AO_BINARY_UNAVAILABLE"}
    status = command_json(binary, "status", "--json")
    if not isinstance(status, dict) or status.get("ready") != "ready":
        return {"error": "AO_NOT_READY"}

    params = urlencode({"project": AO_PROJECT_ID, "active": "false"})
    sessions_doc = api_document(status, f"/api/v1/sessions?{params}")
    sessions = sessions_doc.get("sessions", []) if isinstance(sessions_doc, dict) else []
    display_name = display_name_for(target_event_id)
    matches = [
        item
        for item in sessions
        if isinstance(item, dict)
        and item.get("projectId") == AO_PROJECT_ID
        and item.get("displayName") == display_name
    ]
    if len(matches) != 1:
        return {
            "error": "SESSION_MATCH_COUNT",
            "session_match_count": len(matches),
            "display_name": display_name,
        }

    listed = matches[0]
    session_id = listed.get("id")
    if not isinstance(session_id, str) or not session_id:
        return {"error": "SESSION_ID_UNAVAILABLE"}

    session_doc = api_document(status, f"/api/v1/sessions/{quote(session_id, safe='')}")
    session = session_doc.get("session", {}) if isinstance(session_doc, dict) else {}
    snapshot = api_document(
        status,
        f"/api/v1/sessions/{quote(session_id, safe='')}/conversation?limit=100",
    )
    messages = snapshot.get("messages", []) if isinstance(snapshot, dict) else []
    expected_marker = marker_for(target_event_id)
    exact_provider_marker = any(
        isinstance(message, dict)
        and message.get("role") == "assistant"
        and message.get("origin") == "provider"
        and message.get("text") == expected_marker
        for message in messages
    )
    settings = snapshot.get("settings", {}) if isinstance(snapshot, dict) else {}

    return {
        "error": "none",
        "session_match_count": 1,
        "session_id": session_id,
        "session_harness": session.get("harness", "") if isinstance(session, dict) else "",
        "session_model": session.get("model", "") if isinstance(session, dict) else "",
        "session_mode": session.get("mode", "") if isinstance(session, dict) else "",
        "session_status": session.get("status", "") if isinstance(session, dict) else "",
        "conversation_available": isinstance(snapshot, dict),
        "conversation_session_match": isinstance(snapshot, dict)
        and snapshot.get("sessionId") == session_id,
        "conversation_harness": snapshot.get("harness", "")
        if isinstance(snapshot, dict)
        else "",
        "conversation_model_setting": settings.get("model", "")
        if isinstance(settings, dict)
        else "",
        "conversation_message_count": len(messages) if isinstance(messages, list) else 0,
        "exact_assistant_provider_marker": exact_provider_marker,
    }


def render(target_event_id, result):
    ordered = [
        ("target_event_id", target_event_id),
        ("error", result.get("error", "UNKNOWN")),
        ("session_match_count", result.get("session_match_count", 0)),
        ("session_id", result.get("session_id", "")),
        ("session_harness", result.get("session_harness", "")),
        ("session_model", result.get("session_model", "")),
        ("session_mode", result.get("session_mode", "")),
        ("session_status", result.get("session_status", "")),
        ("conversation_available", str(result.get("conversation_available", False)).lower()),
        ("conversation_session_match", str(result.get("conversation_session_match", False)).lower()),
        ("conversation_harness", result.get("conversation_harness", "")),
        ("conversation_model_setting", result.get("conversation_model_setting", "")),
        ("conversation_message_count", result.get("conversation_message_count", 0)),
        ("exact_assistant_provider_marker", str(result.get("exact_assistant_provider_marker", False)).lower()),
    ]
    return "AICTRL_PRE003_DIAG_RESULT_V1\n" + "\n".join(
        f"{key}: {value}" for key, value in ordered
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--target-event-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    if not admitted_diag_event(args.event, args.target_event_id):
        print("DIAG_REJECTED", file=sys.stderr)
        return 2
    result = diagnostic(args.target_event_id)
    Path(args.output).write_text(render(args.target_event_id, result) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
