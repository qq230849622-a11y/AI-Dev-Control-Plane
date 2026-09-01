"""One-shot no-model final verification for PRE-005 PR #35."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = "qq230849622-a11y/AI-Dev-Control-Plane"
ISSUE = 29
PR = 35
BRANCH = "aictrl/pre-005"
EXPECTED_HEAD = "08c284c3d9573c6bbb1a0c39474aeb412c5a55a7"
PRE_REWORK_HEAD = "ed08ff33341d4d3f7a4d086dc65d79ae25848876"
ORIGINAL_BASE = "9fbb91b560bebdeb037c6367c39553ae3a4f4d23"
EXPECTED_BODY = "\n".join([
    "AICTRL_PRE005_FINAL_VERIFY_V1",
    "project_key: AI_DEV_CONTROL_PLANE",
    f"repo: {REPO}",
    "task_id: PRE-005",
    f"pr_number: {PR}",
    f"head_sha: {EXPECTED_HEAD}",
])
ALLOWED_TOTAL = {
    ".github/workflows/aictrl-task-dispatch.yml",
    "scripts/aictrl_task_dispatch.py",
    "scripts/aictrl_task_dispatch_bootstrap.py",
    "tests/test_task_dispatch.py",
    "README.md",
}
EXPECTED_R1 = {"scripts/aictrl_task_dispatch.py", "tests/test_task_dispatch.py"}


class VerifyError(RuntimeError):
    pass


def run(command, cwd, *, env=None, timeout=900):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise VerifyError(f"COMMAND_FAILED:{' '.join(command)}")
    return result.stdout.strip()


def write(path, status, reason, focused="", full=""):
    Path(path).write_text("\n".join([
        "AICTRL_PRE005_FINAL_VERIFY_RESULT_V1",
        "project_key: AI_DEV_CONTROL_PLANE",
        f"repo: {REPO}",
        "task_id: PRE-005",
        f"pr_number: {PR}",
        f"head_sha: {EXPECTED_HEAD}",
        f"focused_tests: {focused}",
        f"full_tests: {full}",
        f"reason: {reason}",
        f"status: {status}",
    ]) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    focused = full = ""
    try:
        event = json.loads(Path(args.event).read_text(encoding="utf-8"))
        if (
            event.get("action") != "created"
            or event.get("repository", {}).get("full_name") != REPO
            or event.get("sender", {}).get("login") != "qq230849622-a11y"
            or event.get("issue", {}).get("number") != ISSUE
            or event.get("comment", {}).get("body") != EXPECTED_BODY
        ):
            raise VerifyError("EVENT_BINDING_MISMATCH")
        candidate = Path(args.candidate).resolve()
        if not candidate.is_dir():
            raise VerifyError("CANDIDATE_MISSING")
        if run(["git", "rev-parse", "HEAD"], candidate) != EXPECTED_HEAD:
            raise VerifyError("HEAD_MISMATCH")
        if run(["git", "branch", "--show-current"], candidate) != BRANCH:
            raise VerifyError("BRANCH_MISMATCH")

        pr = json.loads(run([
            "gh", "pr", "view", str(PR), "--repo", REPO,
            "--json", "number,url,isDraft,state,headRefName,baseRefName,headRefOid"
        ], candidate))
        if (
            pr.get("number") != PR or pr.get("state") != "OPEN" or pr.get("isDraft") is not False
            or pr.get("headRefName") != BRANCH or pr.get("baseRefName") != "master"
            or pr.get("headRefOid") != EXPECTED_HEAD
        ):
            raise VerifyError("PR_STATE_MISMATCH")

        total = {line for line in run(["git", "diff", "--name-only", f"{ORIGINAL_BASE}...HEAD"], candidate).splitlines() if line}
        if not total or not total.issubset(ALLOWED_TOTAL):
            raise VerifyError("TOTAL_SCOPE_VIOLATION")
        r1 = {line for line in run(["git", "diff", "--name-only", f"{PRE_REWORK_HEAD}...HEAD"], candidate).splitlines() if line}
        if r1 != EXPECTED_R1:
            raise VerifyError("R1_SCOPE_MISMATCH")

        env = os.environ.copy()
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        focused_output = run([sys.executable, "-m", "pytest", "-q", "tests/test_task_dispatch.py"], candidate, env=env, timeout=600)
        focused = focused_output.splitlines()[-1] if focused_output else "PASS"
        full_output = run([sys.executable, "-m", "pytest", "-q"], candidate, env=env, timeout=900)
        full = full_output.splitlines()[-1] if full_output else "PASS"
        if run(["git", "status", "--porcelain", "--untracked-files=no"], candidate):
            raise VerifyError("TRACKED_DIRTY_AFTER_TESTS")
        write(args.result, "PASS", "none", focused, full)
        return 0
    except (VerifyError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        reason = str(exc) if str(exc) else exc.__class__.__name__
        write(args.result, "FAIL", reason, focused, full)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
