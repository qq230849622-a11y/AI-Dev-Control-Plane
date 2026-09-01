import argparse
import json
from pathlib import Path, PurePosixPath

import scripts.aictrl_pre004_dispatch as base


ISSUE_NUMBER = 20
PROJECT_ID = "ai-dev-control-plane"
SESSION_ID = "ai-dev-control-plane-7"
BRANCH = "ctrl/prod-001-generic-task-dispatch"
EXPECTED_HEAD = "53565c28840f3d1cd9585934b319c1d5f9278701"
EXPECTED_COMMENT = "\n".join(
    [
        "AICTRL_PROD001_WORKTREE_CLEANUP_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        "repo: qq230849622-a11y/AI-Dev-Control-Plane",
        "task_id: CTRL-PROD-001",
        f"session_id: {SESSION_ID}",
        f"branch: {BRANCH}",
        f"head_sha: {EXPECTED_HEAD}",
    ]
)


class CleanupFailure(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def fail(code):
    raise CleanupFailure(code)


def admit(path):
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CleanupFailure("EVENT_UNREADABLE") from exc
    if (
        not isinstance(event, dict)
        or event.get("action") != "created"
        or event.get("repository", {}).get("full_name") != base.REPOSITORY
        or event.get("sender", {}).get("login") != "qq230849622-a11y"
        or event.get("issue", {}).get("number") != ISSUE_NUMBER
        or event.get("comment", {}).get("body") != EXPECTED_COMMENT
    ):
        fail("EVENT_BINDING_MISMATCH")


def run_git(repo, *args, code="GIT_COMMAND_FAILED"):
    result = base.run(["git", *args], cwd=repo, timeout=60)
    if result.returncode != 0:
        fail(code)
    return result.stdout.strip()


def parse_worktrees(text):
    records = []
    current = {}
    for line in text.splitlines() + [""]:
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value.strip()
    return records


def is_disposable_bytecode_status(line):
    if not line.startswith("?? "):
        return False
    relative = line[3:].strip().replace("\\", "/")
    path = PurePosixPath(relative)
    return (
        path.suffix == ".pyc"
        and "__pycache__" in path.parts
        and ".." not in path.parts
        and not path.is_absolute()
    )


def session_state(status, session_id):
    document = base.api_document(status, f"/api/v1/sessions/{session_id}")
    session = document.get("session") if isinstance(document, dict) else None
    return session if isinstance(session, dict) else None


def active_sessions(status):
    document = base.api_document(status, f"/api/v1/sessions?project={PROJECT_ID}&active=true")
    values = document.get("sessions") if isinstance(document, dict) else None
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else None


def remove_disposable_bytecode(workspace, status_lines):
    root = Path(workspace).resolve()
    removed = 0
    candidate_dirs = set()
    for line in status_lines:
        relative = line[3:].strip().replace("\\", "/")
        target = (root / Path(*PurePosixPath(relative).parts)).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            fail("DISPOSABLE_PATH_ESCAPE")
        if not target.is_file():
            fail("DISPOSABLE_FILE_MISSING")
        if target.suffix != ".pyc" or target.parent.name != "__pycache__":
            fail("NON_DISPOSABLE_DIRTY_PATH")
        target.unlink()
        removed += 1
        candidate_dirs.add(target.parent)

    for directory in sorted(candidate_dirs, key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed


def execute(event_path, result_path):
    lines = [
        "AICTRL_PROD001_WORKTREE_CLEANUP_RESULT_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        f"repo: {base.REPOSITORY}",
        "task_id: CTRL-PROD-001",
        f"session_id: {SESSION_ID}",
        f"branch: {BRANCH}",
        f"head_sha: {EXPECTED_HEAD}",
    ]
    try:
        admit(event_path)
        binary = base.ao_binary()
        if not binary.is_file():
            fail("AO_BINARY_UNAVAILABLE")
        status = base.ensure_ao_ready(binary)
        active = active_sessions(status)
        if active is None:
            fail("ACTIVE_SESSION_STATE_UNAVAILABLE")
        if active:
            fail("ACTIVE_SESSION_PRESENT")
        session = session_state(status, SESSION_ID)
        if not session:
            fail("TARGET_SESSION_UNAVAILABLE")
        if session.get("status") != "terminated" and session.get("isTerminated") is not True:
            fail("TARGET_SESSION_NOT_TERMINATED")

        project_doc = base.command_json(binary, "project", "get", PROJECT_ID, "--json")
        project = project_doc.get("project") if isinstance(project_doc, dict) else None
        main_path = Path(project.get("path", "")) if isinstance(project, dict) else Path()
        if not main_path.is_dir():
            fail("AO_PROJECT_PATH_UNAVAILABLE")
        if run_git(main_path, "status", "--porcelain", code="MAIN_STATUS_FAILED"):
            fail("MAIN_DIRTY")

        local_head = run_git(main_path, "rev-parse", f"refs/heads/{BRANCH}", code="LOCAL_BRANCH_UNAVAILABLE")
        if local_head != EXPECTED_HEAD:
            fail("LOCAL_BRANCH_HEAD_MISMATCH")
        remote = base.run(["git", "ls-remote", "--heads", "origin", BRANCH], cwd=main_path, timeout=60)
        parts = remote.stdout.strip().split() if remote.returncode == 0 else []
        if len(parts) < 2 or parts[0] != EXPECTED_HEAD:
            fail("REMOTE_BRANCH_HEAD_MISMATCH")

        records = parse_worktrees(run_git(main_path, "worktree", "list", "--porcelain"))
        matches = [item for item in records if item.get("branch") == f"refs/heads/{BRANCH}"]
        if len(matches) != 1:
            fail("TARGET_WORKTREE_COUNT_MISMATCH")
        workspace = Path(matches[0].get("worktree", ""))
        if not workspace.is_dir():
            fail("TARGET_WORKTREE_MISSING")
        if run_git(workspace, "rev-parse", "HEAD") != EXPECTED_HEAD:
            fail("TARGET_WORKTREE_HEAD_MISMATCH")
        if run_git(workspace, "branch", "--show-current") != BRANCH:
            fail("TARGET_WORKTREE_BRANCH_MISMATCH")
        tracked_diff = run_git(workspace, "diff", "--name-status", "--no-renames", "HEAD")
        if tracked_diff:
            fail("TRACKED_WORKTREE_DELTA_PRESENT")
        status_text = run_git(workspace, "status", "--porcelain=v1", "--untracked-files=all")
        dirty_lines = [line for line in status_text.splitlines() if line.strip()]
        if not dirty_lines:
            fail("NO_CLEANUP_REQUIRED")
        if not all(is_disposable_bytecode_status(line) for line in dirty_lines):
            fail("NON_DISPOSABLE_DIRTY_PATH")

        removed = remove_disposable_bytecode(workspace, dirty_lines)
        if run_git(workspace, "status", "--porcelain=v1", "--untracked-files=all"):
            fail("WORKTREE_STILL_DIRTY_AFTER_BYTECODE_REMOVAL")

        removal = base.run(["git", "worktree", "remove", str(workspace)], cwd=main_path, timeout=120)
        if removal.returncode != 0:
            fail("WORKTREE_REMOVE_FAILED")
        remaining = parse_worktrees(run_git(main_path, "worktree", "list", "--porcelain"))
        if any(item.get("branch") == f"refs/heads/{BRANCH}" for item in remaining):
            fail("WORKTREE_REGISTRATION_REMAINS")
        if workspace.exists():
            fail("WORKTREE_PATH_REMAINS")
        if run_git(main_path, "rev-parse", f"refs/heads/{BRANCH}") != EXPECTED_HEAD:
            fail("LOCAL_BRANCH_CHANGED")
        remote_after = base.run(["git", "ls-remote", "--heads", "origin", BRANCH], cwd=main_path, timeout=60)
        after_parts = remote_after.stdout.strip().split() if remote_after.returncode == 0 else []
        if len(after_parts) < 2 or after_parts[0] != EXPECTED_HEAD:
            fail("REMOTE_BRANCH_CHANGED")
        if run_git(main_path, "status", "--porcelain", code="MAIN_STATUS_FAILED"):
            fail("MAIN_DIRTY_AFTER_CLEANUP")

        lines.extend([f"bytecode_removed: {removed}", "worktree_reclaimed: true", "status: PASS"])
    except CleanupFailure as exc:
        lines.extend([f"reason: {exc.code}", "status: FAIL"])
    except Exception:
        lines.extend(["reason: UNEXPECTED_CLEANUP_ERROR", "status: FAIL"])
    Path(result_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if lines[-1] == "status: PASS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    return execute(args.event, args.result)


if __name__ == "__main__":
    raise SystemExit(main())
