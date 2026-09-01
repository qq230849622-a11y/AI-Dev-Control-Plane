import argparse
import json
from pathlib import Path

import scripts.aictrl_pre004_dispatch as base


ISSUE_NUMBER = 20
PROJECT_ID = "ai-dev-control-plane"
BRANCH = "ctrl/prod-001-generic-task-dispatch"
EXPECTED_COMMENT = "\n".join(
    [
        "AICTRL_PROD001_R1_DIAG_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        "repo: qq230849622-a11y/AI-Dev-Control-Plane",
        "task_id: CTRL-PROD-001",
        "probe: worktree-state",
    ]
)


class DiagFailure(RuntimeError):
    pass


def admit(path):
    event = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(event, dict)
        or event.get("action") != "created"
        or event.get("repository", {}).get("full_name") != base.REPOSITORY
        or event.get("sender", {}).get("login") != "qq230849622-a11y"
        or event.get("issue", {}).get("number") != ISSUE_NUMBER
        or event.get("comment", {}).get("body") != EXPECTED_COMMENT
    ):
        raise DiagFailure("EVENT_BINDING_MISMATCH")


def run_git(repo, *args):
    result = base.run(["git", *args], cwd=repo, timeout=60)
    if result.returncode != 0:
        raise DiagFailure("GIT_DIAG_FAILED")
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


def api_sessions(status, active):
    value = base.api_document(status, f"/api/v1/sessions?project={PROJECT_ID}&active={'true' if active else 'false'}")
    sessions = value.get("sessions") if isinstance(value, dict) else None
    if not isinstance(sessions, list):
        return []
    output = []
    for item in sessions:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "")
        display = str(item.get("displayName") or "")
        state = str(item.get("status") or "")
        if sid:
            output.append(f"{sid}:{display}:{state}")
    return output


def bounded_status_lines(text, limit=20):
    lines = []
    for raw in text.splitlines():
        value = raw.strip()
        if not value:
            continue
        lines.append(value[:300])
        if len(lines) >= limit:
            break
    return lines


def execute(event_path, result_path):
    lines = [
        "AICTRL_PROD001_R1_DIAG_RESULT_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        f"repo: {base.REPOSITORY}",
        "task_id: CTRL-PROD-001",
        f"branch: {BRANCH}",
    ]
    try:
        admit(event_path)
        binary = base.ao_binary()
        if not binary.is_file():
            raise DiagFailure("AO_BINARY_UNAVAILABLE")
        status = base.ensure_ao_ready(binary)
        project_doc = base.command_json(binary, "project", "get", PROJECT_ID, "--json")
        project = project_doc.get("project") if isinstance(project_doc, dict) else None
        main_path = Path(project.get("path", "")) if isinstance(project, dict) else Path()
        if not main_path.is_dir():
            raise DiagFailure("AO_PROJECT_PATH_UNAVAILABLE")

        worktrees = parse_worktrees(run_git(main_path, "worktree", "list", "--porcelain"))
        target = [item for item in worktrees if item.get("branch") == f"refs/heads/{BRANCH}"]
        local = base.run(["git", "rev-parse", "--verify", f"refs/heads/{BRANCH}"], cwd=main_path)
        local_head = local.stdout.strip() if local.returncode == 0 else "absent"
        remote = base.run(["git", "ls-remote", "--heads", "origin", BRANCH], cwd=main_path)
        remote_parts = remote.stdout.strip().split() if remote.returncode == 0 else []
        remote_head = remote_parts[0] if remote_parts else "absent"

        lines.extend(
            [
                f"local_branch_head: {local_head}",
                f"remote_branch_head: {remote_head}",
                f"target_branch_worktree_count: {len(target)}",
            ]
        )
        if len(target) == 1:
            record = target[0]
            path = Path(record.get("worktree", ""))
            exists = path.is_dir()
            lines.append(f"target_worktree_label: {path.name if path.name else 'unknown'}")
            lines.append(f"target_worktree_exists: {'true' if exists else 'false'}")
            lines.append(f"target_worktree_record_head: {record.get('HEAD', '')}")
            if exists:
                branch_result = base.run(["git", "branch", "--show-current"], cwd=path)
                head_result = base.run(["git", "rev-parse", "HEAD"], cwd=path)
                status_result = base.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=path)
                lines.append(f"target_worktree_current_branch: {branch_result.stdout.strip() if branch_result.returncode == 0 else 'unreadable'}")
                lines.append(f"target_worktree_actual_head: {head_result.stdout.strip() if head_result.returncode == 0 else 'unreadable'}")
                dirty = status_result.returncode != 0 or bool(status_result.stdout.strip())
                lines.append(f"target_worktree_dirty: {'true' if dirty else 'false'}")
                status_lines = bounded_status_lines(status_result.stdout if status_result.returncode == 0 else "STATUS_UNREADABLE")
                lines.append(f"target_worktree_status_count: {len(status_lines)}")
                for index, value in enumerate(status_lines, start=1):
                    lines.append(f"target_worktree_status_{index}: {value}")
                diff_names = bounded_status_lines(run_git(path, "diff", "--name-status", "--no-renames", "HEAD"))
                lines.append(f"target_worktree_diff_count: {len(diff_names)}")
                for index, value in enumerate(diff_names, start=1):
                    lines.append(f"target_worktree_diff_{index}: {value}")
        prune = base.run(["git", "worktree", "prune", "--dry-run", "--verbose"], cwd=main_path)
        prune_text = " | ".join(line.strip() for line in prune.stdout.splitlines() if line.strip())
        lines.append(f"prune_dry_run: {prune_text[:500] if prune_text else 'none'}")
        lines.append(f"active_sessions: {','.join(api_sessions(status, True)) or 'none'}")
        lines.append(f"terminated_sessions: {','.join(api_sessions(status, False)) or 'none'}")
        lines.append("status: PASS")
    except Exception as exc:
        reason = str(exc) if isinstance(exc, DiagFailure) else "UNEXPECTED_DIAG_ERROR"
        lines.extend([f"reason: {reason}", "status: FAIL"])
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
