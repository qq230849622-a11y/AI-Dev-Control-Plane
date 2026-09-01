import argparse
import json
import time
from pathlib import Path

import scripts.aictrl_pre004_dispatch as base


def git_top_level(path):
    return Path(base.git(path, "rev-parse", "--show-toplevel", code="GIT_TOP_LEVEL_FAILED")).resolve()


def git_dir(path):
    return Path(base.git(path, "rev-parse", "--path-format=absolute", "--git-dir", code="GIT_DIR_FAILED")).resolve()


def verify_isolated_workspace(workspace, main_path):
    base.verify_isolated_workspace(workspace, main_path)
    workspace = Path(workspace).resolve()
    main_path = Path(main_path).resolve()
    workspace_top = git_top_level(workspace)
    main_top = git_top_level(main_path)
    if workspace != workspace_top or main_path != main_top:
        raise base.ProbeFailure("WORKTREE_ROOT_UNVERIFIED")
    if workspace_top == main_top:
        raise base.ProbeFailure("WORKTREE_ROOT_NOT_DISTINCT")
    if git_dir(workspace) == git_dir(main_path):
        raise base.ProbeFailure("WORKTREE_GITDIR_NOT_DISTINCT")
    if base.git_common_dir(workspace) != base.git_common_dir(main_path):
        raise base.ProbeFailure("WORKTREE_REPOSITORY_MISMATCH")


def verify_pr_and_git(workspace, main_path, synced_head):
    pr, worker_head = base.verify_pr_and_git(workspace, main_path, synced_head)
    result = base.run(
        [
            "gh", "pr", "list", "--repo", base.REPOSITORY,
            "--head", base.BRANCH, "--state", "all",
            "--json", "number,url,isDraft,state,headRefName,baseRefName",
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise base.ProbeFailure("PR_LOOKUP_FAILED")
    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise base.ProbeFailure("PR_LOOKUP_FAILED") from exc
    if len(prs) != 1:
        raise base.ProbeFailure("PR_COUNT_ALL_STATES_MISMATCH")
    only = prs[0]
    if (
        only.get("state") != "OPEN"
        or only.get("isDraft") is not False
        or only.get("headRefName") != base.BRANCH
        or only.get("baseRefName") != "master"
        or only.get("number") != pr.get("number")
    ):
        raise base.ProbeFailure("PR_STATE_MISMATCH")
    return pr, worker_head


def cleanup_session_confirmed(binary, status, session_id):
    if not base.command_success(binary, "session", "kill", session_id, "--project", base.PROJECT_ID):
        return False
    for _ in range(20):
        document = base.api_document(status, f"/api/v1/sessions/{session_id}")
        session = document.get("session") if isinstance(document, dict) else None
        if isinstance(session, dict) and (
            session.get("status") == "terminated" or session.get("isTerminated") is True
        ):
            return True
        time.sleep(0.5)
    return False


def execute(event_path, result_path):
    session_id = ""
    binary = None
    status = None
    failure = None
    pass_data = None
    try:
        base.admit_event(event_path)
        defender_before = base.defender_fingerprint()
        binary = base.ao_binary()
        if not binary.is_file():
            raise base.ProbeFailure("AO_BINARY_UNAVAILABLE")
        status = base.ensure_ao_ready(binary)
        project_doc = base.command_json(binary, "project", "get", base.PROJECT_ID, "--json")
        project = project_doc.get("project") if isinstance(project_doc, dict) else None
        if not isinstance(project, dict):
            raise base.ProbeFailure("AO_PROJECT_UNAVAILABLE")
        main_path, synced_head = base.safe_sync_main(project)
        base.reject_existing_probe_artifacts(main_path)
        if not base.has_chatgpt_login():
            raise base.ProbeFailure("CODEX_CHATGPT_LOGIN_UNVERIFIED")
        catalog = base.api_document(status, f"/api/v1/agents/codex/models?projectId={base.PROJECT_ID}")
        if not base.catalog_has_terra(catalog):
            raise base.ProbeFailure("TERRA_MODEL_CATALOG_UNAVAILABLE")
        session_id = base.spawn_worker(binary)
        workspace = base.workspace_path(status, session_id)
        verify_isolated_workspace(workspace, main_path)
        base.wait_for_worker(status, session_id)
        pr, worker_head = verify_pr_and_git(workspace, main_path, synced_head)
        if base.defender_fingerprint() != defender_before:
            raise base.ProbeFailure("DEFENDER_NEW_DETECTION")
        pass_data = (pr, synced_head, worker_head)
    except base.ProbeFailure as exc:
        failure = exc.code
    except Exception:
        failure = "UNEXPECTED_RUNTIME_ERROR"

    if session_id:
        if binary is None or status is None or not cleanup_session_confirmed(binary, status, session_id):
            failure = "SESSION_CLEANUP_FAILED"

    if failure:
        base.write_fail(result_path, failure, session_id)
        return 1
    pr, synced_head, worker_head = pass_data
    base.write_pass(result_path, session_id, pr, synced_head, worker_head)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    return execute(args.event, args.result)


if __name__ == "__main__":
    raise SystemExit(main())
