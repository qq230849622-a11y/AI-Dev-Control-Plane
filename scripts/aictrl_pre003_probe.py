import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from aictrl.pre003 import (
    EXPECTED_MODEL,
    EXPECTED_REPOSITORY,
    build_probe_marker,
    has_exact_session_result,
    is_luna_session,
)


AO_PROJECT_ID = "ai-dev-control-plane"


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
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def command_success(binary, *arguments):
    try:
        return (
            subprocess.run([str(binary), *arguments], capture_output=True, text=True, check=False).returncode
            == 0
        )
    except OSError:
        return False


def is_ready(status):
    return isinstance(status, dict) and status.get("ready") == "ready" and status.get("health") == "ok"


def ensure_ready(binary):
    if is_ready(command_json(binary, "status", "--json")):
        return True
    if not command_success(binary, "start"):
        return False
    for _ in range(10):
        time.sleep(2)
        if is_ready(command_json(binary, "status", "--json")):
            return True
    return False


def find_authorized_codex(agent_catalog):
    if not isinstance(agent_catalog, dict):
        return False
    return any(
        agent.get("id") == "codex" and agent.get("authStatus") == "authorized"
        for agent in agent_catalog.get("authorized", [])
        if isinstance(agent, dict)
    )


def project_path(project):
    path = project.get("project", {}).get("path") if isinstance(project, dict) else None
    return Path(path) if isinstance(path, str) and path else None


def clean_head(path):
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=False
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=path, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if head.returncode != 0 or status.returncode != 0 or status.stdout:
        return None
    value = head.stdout.strip()
    return value if len(value) == 40 else None


def session_id_by_name(document, session_name):
    if not isinstance(document, dict) or not isinstance(session_name, str):
        return None
    matches = [
        session.get("id")
        for session in document.get("sessions", [])
        if isinstance(session, dict)
        and session.get("projectId") == AO_PROJECT_ID
        and session.get("displayName") == session_name
        and isinstance(session.get("id"), str)
        and session["id"]
    ]
    return matches[0] if len(matches) == 1 else None


def catalog_has_luna(catalog):
    return (
        isinstance(catalog, dict)
        and catalog.get("agentId") == "codex"
        and isinstance(catalog.get("models"), list)
        and any(isinstance(model, dict) and model.get("id") == EXPECTED_MODEL for model in catalog["models"])
    )


def is_chatgpt_login_status(output):
    return isinstance(output, str) and output.strip() == "Logged in using ChatGPT"


def has_chatgpt_login():
    try:
        result = subprocess.run(
            ["codex", "login", "status"], capture_output=True, text=True, check=False
        )
    except OSError:
        return False
    return result.returncode == 0 and (
        is_chatgpt_login_status(result.stdout)
        or is_chatgpt_login_status(result.stderr)
    )


def api_document(status, path):
    port = status.get("port") if isinstance(status, dict) else None
    if not isinstance(port, int):
        return None
    try:
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
            document = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def session_view(status, session_id):
    return api_document(status, f"/api/v1/sessions/{quote(session_id, safe='')}")


def conversation_snapshot(status, session_id):
    return api_document(status, f"/api/v1/sessions/{quote(session_id, safe='')}/conversation?limit=100")


def codex_model_catalog(status):
    return api_document(status, f"/api/v1/agents/codex/models?projectId={quote(AO_PROJECT_ID, safe='')}")


def is_bound_luna_conversation(snapshot, session_id):
    # Runtime diagnostic evidence from AO v0.12.10 showed that the terminated
    # SessionView can expose an empty `model` even though this exact bound Codex
    # conversation reports `settings.model = gpt-5.6-luna`.  Keep session
    # identity/harness proof separate, and use the provider conversation's
    # selected model as the explicit runtime model proof.
    return (
        isinstance(snapshot, dict)
        and snapshot.get("sessionId") == session_id
        and snapshot.get("harness") == "codex"
        and isinstance(snapshot.get("settings"), dict)
        and snapshot["settings"].get("model") == EXPECTED_MODEL
    )


def session_workspace(status, session_id):
    document = api_document(
        status, f"/api/v1/desktop/sessions/{quote(session_id, safe='')}/workspace"
    )
    value = document.get("workspacePath") if isinstance(document, dict) else None
    return Path(value) if isinstance(value, str) and value else None


def git_common_dir(path):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).resolve()


def is_isolated_worktree(workspace, main_path):
    if not workspace or not workspace.is_dir() or workspace.resolve() == main_path.resolve():
        return False
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return (
        result.returncode == 0
        and result.stdout.strip() == "true"
        and git_common_dir(workspace) == git_common_dir(main_path)
    )


def probe(binary, event_id):
    marker = build_probe_marker(event_id)
    if not binary.is_file():
        return "AO_BINARY_UNAVAILABLE", None, marker, None, None
    if not ensure_ready(binary):
        return "AO_NOT_READY", None, marker, None, None
    status = command_json(binary, "status", "--json")

    project = command_json(binary, "project", "get", AO_PROJECT_ID, "--json")
    if not isinstance(project, dict) or not isinstance(project.get("project"), dict) or project["project"].get("repo") != f"https://github.com/{EXPECTED_REPOSITORY}.git":
        return "AO_PROJECT_MISMATCH", None, marker, None, None
    main_path = project_path(project)
    main_head = clean_head(main_path) if main_path else None
    if main_head is None:
        return "CONTROL_PLANE_MAIN_DIRTY", None, marker, None, None

    agents = command_json(binary, "agent", "ls", "--json")
    if agents is None or not find_authorized_codex(agents):
        return "CODEX_NOT_AUTHORIZED", None, marker, main_head, None
    if not has_chatgpt_login():
        return "CODEX_CHATGPT_LOGIN_UNVERIFIED", None, marker, main_head, None
    if not catalog_has_luna(codex_model_catalog(status)):
        return "LUNA_MODEL_CATALOG_UNAVAILABLE", None, marker, main_head, None

    session_name = f"pre003-{marker.rsplit('_', 1)[-1][:12]}"
    prompt = (
        "PRE-003 read-only transport probe. Do not edit files, commit, push, open a PR, or merge. "
        f"Respond with exactly this marker and nothing else: {marker}"
    )
    if not command_success(
        binary,
        "spawn",
        "--project",
        AO_PROJECT_ID,
        "--harness",
        "codex",
        "--model",
        EXPECTED_MODEL,
        "--mode",
        "chat",
        "--name",
        session_name,
        "--prompt",
        prompt,
    ):
        return "LUNA_MODEL_UNAVAILABLE", None, marker, main_head, None

    session_id = session_id_by_name(api_document(status, "/api/v1/sessions"), session_name)
    if session_id is None:
        return "AO_SESSION_ID_UNAVAILABLE", None, marker, main_head, None
    workspace = session_workspace(status, session_id)
    if not is_isolated_worktree(workspace, main_path):
        command_success(binary, "session", "kill", session_id, "--project", AO_PROJECT_ID)
        return "AO_WORKTREE_UNVERIFIED", session_id, marker, main_head, None

    try:
        for _ in range(60):
            session = session_view(status, session_id)
            if session is None:
                return "AO_SESSION_UNOBSERVABLE", session_id, marker, main_head, str(workspace)
            if not is_luna_session(session):
                return "LUNA_MODEL_UNVERIFIED", session_id, marker, main_head, str(workspace)
            snapshot = conversation_snapshot(status, session_id)
            if not is_bound_luna_conversation(snapshot, session_id):
                return "LUNA_MODEL_UNVERIFIED", session_id, marker, main_head, str(workspace)
            if has_exact_session_result(snapshot, marker):
                final_head = clean_head(main_path)
                if final_head != main_head:
                    return "CONTROL_PLANE_MAIN_CHANGED", session_id, marker, main_head, str(workspace)
                return "PASS", session_id, marker, main_head, str(workspace)
            state = session["session"].get("status")
            if state == "terminated":
                return "RESULT_MARKER_UNOBSERVABLE", session_id, marker, main_head, str(workspace)
            time.sleep(5)
        return "AO_SESSION_TIMEOUT", session_id, marker, main_head, str(workspace)
    finally:
        command_success(binary, "session", "kill", session_id, "--project", AO_PROJECT_ID)


def write_output(path, status, reason, session_id, marker, main_head, worktree_path):
    lines = [
        f"status={status}",
        f"reason={reason}",
        f"session_id={session_id or ''}",
        f"result_marker={marker}",
        f"main_head={main_head or ''}",
        f"worktree_path={worktree_path or ''}",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)
    marker = build_probe_marker(arguments.event_id)
    if not arguments.event_id.strip():
        write_output(arguments.output, "FAIL", "INVALID_EVENT_ID", None, marker, None, None)
        print("FAIL")
        return 0
    reason, session_id, marker, main_head, worktree_path = probe(ao_binary(), arguments.event_id)
    status = "PASS" if reason == "PASS" else "FAIL"
    write_output(arguments.output, status, "" if status == "PASS" else reason, session_id, marker, main_head, worktree_path)
    print(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
