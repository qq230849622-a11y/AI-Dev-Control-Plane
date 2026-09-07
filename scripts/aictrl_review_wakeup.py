"""GitHub adapter for a ChatGPT Work review run; never generates review decisions.

Run from a trusted controller checkout. Do not execute a candidate PR checkout.
Uses the existing authenticated gh context; does not read or print credentials.
"""

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aictrl.registry import load_registry
from aictrl.review_wakeup import (ReviewRejected, admit, persist_decision,
                                  require, strict_json, verify_record)
from aictrl.validator import validate_document

REPO = "qq230849622-a11y/AI-Dev-Control-Plane"
PROJECT = "AI_DEV_CONTROL_PLANE"
LEDGER_BRANCH = "aictrl/controller-decisions"


def api(path, method="GET", payload=None, missing_ok=False):
    require(path.startswith(f"repos/{REPO}/"), "API_REPO_MISMATCH")
    command = ["gh", "api", "--method", method, path]
    if payload is not None:
        command += ["--input", "-"]
    response = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
    if response.returncode:
        if missing_ok and "(HTTP 404)" in response.stderr:
            return None
        raise ReviewRejected("GITHUB_REQUEST_FAILED")
    return strict_json(response.stdout)


def pages(path):
    result = []
    for page in range(1, 101):
        items = api(f"{path}?per_page=100&page={page}")
        require(isinstance(items, list), "GITHUB_LIST_INVALID")
        result.extend(items)
        if len(items) < 100:
            return result
    raise ReviewRejected("GITHUB_PAGINATION_LIMIT")


def binding():
    bindings = load_registry(Path(__file__).resolve().parents[1] / ".ai-control/projects")
    matches = [b for b in bindings if b.repo == REPO and b.project_key == PROJECT and b.enabled]
    require(len(matches) == 1, "PROJECT_BINDING_MISMATCH")
    return matches[0]


def fetch_bundle(comment_id, probe_pr=None):
    require(type(comment_id) is int and comment_id > 0, "COMMENT_ID_INVALID")
    comment = api(f"repos/{REPO}/issues/comments/{comment_id}")
    event = strict_json(comment.get("body", ""))
    require(validate_document(event).valid and event.get("protocol") == "AICTRL_EVENT_V1", "EVENT_INVALID")
    require(event["repo"] == REPO and event["project_key"] == PROJECT, "PROJECT_REPO_MISMATCH")
    number, issue = event["payload"].get("pr_number"), event["payload"].get("source_issue_number")
    require(type(number) is int and number > 0 and type(issue) is int and issue > 0, "EVENT_CONTEXT_MISSING")
    task_issue = api(f"repos/{REPO}/issues/{issue}")
    candidates = []
    authors = {"github-actions[bot]"} | ({REPO.split("/")[0]} if probe_pr == number else set())
    for item in pages(f"repos/{REPO}/issues/{issue}/comments"):
        if item.get("user", {}).get("login") not in authors:
            continue
        try:
            if strict_json(item.get("body", "")) == event:
                candidates.append(item)
        except ReviewRejected:
            continue
    require(bool(candidates), "SOURCE_EVENT_NOT_FOUND")
    source = min(candidates, key=lambda c: c["id"])
    pr = api(f"repos/{REPO}/pulls/{number}")
    files = pages(f"repos/{REPO}/pulls/{number}/files")
    require(pr.get("changed_files") == len(files), "INCOMPLETE_PR_FILES")
    bundle = admit(binding(), comment, source, task_issue, pr, files, probe_pr=probe_pr)
    compare = api(f"repos/{REPO}/compare/{bundle['task']['head_sha']}...{event['head_sha']}")
    require(compare.get("status") in {"ahead", "identical"}, "TASK_BASE_NOT_ANCESTOR")
    return bundle


class GitHubDecisionStore:
    def __init__(self):
        branch = api(f"repos/{REPO}/git/ref/heads/{LEDGER_BRANCH}")
        require(branch.get("ref") == "refs/heads/" + LEDGER_BRANCH, "LEDGER_BRANCH_MISSING")

    def read(self, path):
        data = api(f"repos/{REPO}/contents/{path}?ref={quote(LEDGER_BRANCH, safe='')}", missing_ok=True)
        if data is None:
            return None
        require(data.get("type") == "file" and data.get("encoding") == "base64", "LEDGER_FILE_INVALID")
        return strict_json(base64.b64decode(data["content"]).decode("utf-8"))

    def create(self, path, record):
        # Never pass sha: existing paths must fail rather than be updated.
        payload = {"branch": LEDGER_BRANCH, "message": "Record bound GPT review decision",
                   "content": base64.b64encode(json.dumps(record, sort_keys=True).encode()).decode()}
        api(f"repos/{REPO}/contents/{path}", "PUT", payload)


def mirror_event(path, source_issue):
    event = strict_json(Path(path).read_text(encoding="utf-8-sig"))
    require(validate_document(event).valid and event.get("protocol") == "AICTRL_EVENT_V1", "EVENT_INVALID")
    require(event["repo"] == REPO and event["project_key"] == PROJECT
            and event["event_type"] == "REVIEW_REQUESTED" and event["status"] == "READY_FOR_REVIEW", "EVENT_NOT_REVIEWABLE")
    require(event["payload"].get("source_issue_number") == source_issue, "SOURCE_ISSUE_MISMATCH")
    number = event["payload"].get("pr_number")
    require(type(number) is int and number > 0, "PR_BINDING_MISMATCH")
    pr = api(f"repos/{REPO}/pulls/{number}")
    require(pr.get("head", {}).get("sha") == event["head_sha"] and pr.get("state") == "open"
            and pr.get("draft") is False and pr.get("merged_at") is None
            and pr.get("auto_merge") is None
            and pr.get("html_url") == event["payload"].get("pr_url")
            == f"https://github.com/{REPO}/pull/{number}", "PR_NOT_REVIEWABLE")
    # Issue evidence was posted first by the existing workflow. Require it.
    require(any(c.get("user", {}).get("login") == "github-actions[bot]"
                and c.get("body", "").strip() == json.dumps(event, sort_keys=True)
                for c in pages(f"repos/{REPO}/issues/{source_issue}/comments")), "SOURCE_EVENT_NOT_FOUND")
    body = json.dumps(event, sort_keys=True)
    for item in pages(f"repos/{REPO}/issues/{number}/comments"):
        if item.get("user", {}).get("login") == "github-actions[bot]" and item.get("body", "").strip() == body:
            return item["html_url"]
    return api(f"repos/{REPO}/issues/{number}/comments", "POST", {"body": body})["html_url"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    mirror = sub.add_parser("mirror")
    mirror.add_argument("--event-file", required=True)
    mirror.add_argument("--source-issue", type=int, required=True)
    for name in ("prepare", "record"):
        p = sub.add_parser(name)
        p.add_argument("--comment-id", type=int, required=True)
        p.add_argument("--probe-pr", type=int)
        if name == "record":
            p.add_argument("--decision-file", required=True)
            p.add_argument("--review-input-sha", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "mirror":
            print(mirror_event(args.event_file, args.source_issue))
            return 0
        bundle = fetch_bundle(args.comment_id, args.probe_pr)
        store = GitHubDecisionStore()
        if args.command == "prepare":
            existing = store.read(f"decisions/{bundle['key']}.json")
            if existing is not None:
                verify_record(bundle, existing)
                print(json.dumps({"status": "ALREADY_RECORDED", "record": existing}))
            else:
                print(json.dumps({"status": "REVIEW_REQUIRED", "bundle": bundle}))
            return 0
        decision = strict_json(Path(args.decision_file).read_text(encoding="utf-8"))
        def revalidate():
            fresh = fetch_bundle(args.comment_id, args.probe_pr)
            require(fresh == bundle, "REVIEW_INPUT_CHANGED")
        record, created = persist_decision(bundle, decision, store, revalidate, args.review_input_sha)
        print(json.dumps({"status": "RECORDED" if created else "ALREADY_RECORDED", "record": record}))
        return 0
    except Exception as error:
        print(str(error) if isinstance(error, ReviewRejected) else "REVIEW_RUNTIME_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
