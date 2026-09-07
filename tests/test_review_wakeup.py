import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from aictrl.review_wakeup import (ReviewRejected, admit, event_key, persist_decision,
                                  strict_json, validate_decision)
import scripts.aictrl_review_wakeup as adapter
from test_validator import task_document

REPO = adapter.REPO
NOW = datetime(2026, 9, 7, 0, 5, tzinfo=timezone.utc)


def context():
    task = task_document()
    task.update(owner=REPO.split('/')[0], status="READY")
    event = {"protocol": "AICTRL_EVENT_V1", "project_key": task["project_key"], "repo": REPO,
             "task_id": task["task_id"], "head_sha": "a" * 40, "event_id": "event-1",
             "actor": "AICTRL_CONTROLLER", "event_type": "REVIEW_REQUESTED", "status": "READY_FOR_REVIEW",
             "occurred_at": "2026-09-07T00:00:00Z", "payload": {"pr_number": 10,
             "pr_url": f"https://github.com/{REPO}/pull/10", "source_issue_number": 9}}
    def comment(number, identifier):
        return {"id": identifier, "body": json.dumps(event), "user": {"login": "github-actions[bot]"},
                "issue_url": f"https://api.github.com/repos/{REPO}/issues/{number}",
                "created_at": "2026-09-07T00:00:01Z", "updated_at": "2026-09-07T00:00:01Z"}
    issue = {"number": 9, "url": f"https://api.github.com/repos/{REPO}/issues/9",
             "user": {"login": REPO.split('/')[0]}, "body":
             "AICTRL_TASK_JSON_BEGIN\n" + json.dumps(task) + "\nAICTRL_TASK_JSON_END"}
    pr = {"number": 10, "html_url": event["payload"]["pr_url"], "state": "open", "draft": False,
          "merged_at": None, "auto_merge": None, "base": {"repo": {"full_name": REPO}, "ref": "master", "sha": "b" * 40},
          "head": {"repo": {"full_name": REPO}, "sha": "a" * 40}, "changed_files": 1}
    return [SimpleNamespace(enabled=True, project_key=task["project_key"], repo=REPO, default_branch="master"),
            comment(10, 11), comment(9, 12), issue, pr, [{"filename": "src/aictrl/new.py", "status": "added"}]]


def bundle():
    return admit(*context(), now=NOW)


def decision(b):
    return {"protocol": "AICTRL_DECISION_V1", **{k: b["event"][k] for k in ("project_key", "repo", "task_id", "head_sha")},
            "decision_id": "review-" + b["key"], "actor": "GPT_CONTROLLER", "decision_type": "PR_REVIEW",
            "status": "RECORDED", "decision": "FIX_REQUIRED", "rationale": "Missing explicit integration evidence."}


def test_admit_binds_final_head_without_confusing_task_baseline():
    b = bundle()
    assert b["task"]["head_sha"] != b["event"]["head_sha"]
    assert b["key"] == event_key(b["event"])


@pytest.mark.parametrize("mutation,code", [
    (lambda c: setattr(c[0], "enabled", False), "PROJECT_DISABLED"),
    (lambda c: c[1]["user"].update(login="attacker"), "EVENT_AUTHOR_MISMATCH"),
    (lambda c: c[1].update(updated_at="2026-09-07T00:00:02Z"), "EDITED_EVENT"),
    (lambda c: c[1].update(issue_url="https://api.github.com/repos/other/repo/issues/10"), "COMMENT_REPO_MISMATCH"),
    (lambda c: c[4]["head"].update(sha="b" * 40), "STALE_HEAD"),
    (lambda c: c[4].update(draft=True), "PR_NOT_REVIEWABLE"),
    (lambda c: c[4]["base"]["repo"].update(full_name="other/repo"), "PR_REPO_MISMATCH"),
    (lambda c: c[5][0].update(status="renamed", previous_filename="secret.txt"), "SCOPE_MISMATCH"),
    (lambda c: c[3]["user"].update(login="attacker"), "TASK_SOURCE_MISMATCH"),
])
def test_admission_failures(mutation, code):
    c = context()
    mutation(c)
    with pytest.raises(ReviewRejected, match=code):
        admit(*c, now=NOW)


@pytest.mark.parametrize("field,value,code", [
    ("repo", "other/repo", "PROJECT_REPO_MISMATCH"),
    ("project_key", "OTHER", "PROJECT_REPO_MISMATCH"),
    ("task_id", "OTHER", "TASK_BINDING_MISMATCH"),
    ("event_type", "DECISION", "EVENT_NOT_REVIEWABLE"),
    ("occurred_at", "2026-09-01T00:00:00Z", "STALE_EVENT"),
    ("occurred_at", "2026-09-08T00:00:00Z", "STALE_EVENT"),
])
def test_event_rejections(field, value, code):
    c = context()
    event = json.loads(c[1]["body"])
    event[field] = value
    c[1]["body"] = c[2]["body"] = json.dumps(event)
    with pytest.raises(ReviewRejected, match=code):
        admit(*c, now=NOW)


def test_owner_probe_is_explicit_and_bound_to_one_pr():
    c = context()
    c[1]["user"]["login"] = c[2]["user"]["login"] = REPO.split('/')[0]
    for probe in (None, 99):
        with pytest.raises(ReviewRejected, match="EVENT_AUTHOR_MISMATCH"):
            admit(*c, now=NOW, probe_pr=probe)
    assert admit(*c, now=NOW, probe_pr=10)["comment_id"] == 11


def test_strict_json_and_no_merge_decision():
    for text in ('{"a":1,"a":2}', '{} extra', '{"a":NaN}'):
        with pytest.raises(ReviewRejected):
            strict_json(text)
    b = bundle()
    d = decision(b)
    d["decision"] = "MERGE"
    with pytest.raises(ReviewRejected, match="DECISION_AUTHORITY_EXCEEDED"):
        validate_decision(b, d)


class Store:
    def __init__(self, uncertain=False):
        self.items = {}
        self.lock = threading.Lock()
        self.creates = 0
        self.uncertain = uncertain

    def read(self, path):
        return self.items.get(path)

    def create(self, path, record):
        with self.lock:
            if path in self.items:
                raise RuntimeError("conflict")
            self.items[path] = record
            self.creates += 1
        if self.uncertain:
            raise TimeoutError("response lost after commit")


def test_concurrent_duplicate_and_lost_response_produce_one_decision():
    b = bundle()
    for uncertain in (False, True):
        store = Store(uncertain)
        barrier = threading.Barrier(2)
        def write(_):
            return persist_decision(b, decision(b), store, barrier.wait, b["review_input_sha256"])
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(write, range(2)))
        assert store.creates == 1
        assert results[0][0] == results[1][0]
        assert persist_decision(b, decision(b), store, lambda: None, b["review_input_sha256"])[1] is False


def test_unconfirmed_write_corrupt_ledger_and_stale_revalidation_fail():
    b = bundle()
    store = Store()
    store.create = lambda *a: (_ for _ in ()).throw(TimeoutError())
    with pytest.raises(ReviewRejected, match="DECISION_WRITE_UNCONFIRMED"):
        persist_decision(b, decision(b), store, lambda: None, b["review_input_sha256"])
    store.items[f"decisions/{b['key']}.json"] = {"event": {}}
    with pytest.raises(ReviewRejected, match="LEDGER_EVENT_MISMATCH"):
        persist_decision(b, decision(b), store, lambda: None, b["review_input_sha256"])
    def stale():
        raise ReviewRejected("STALE_HEAD")
    with pytest.raises(ReviewRejected, match="STALE_HEAD"):
        persist_decision(b, decision(b), Store(), stale, b["review_input_sha256"])


@pytest.mark.parametrize("kind", ["task", "base", "files"])
def test_record_binds_the_snapshot_actually_reviewed(kind):
    old = bundle()
    c = context()
    if kind == "task":
        task = old["task"].copy()
        task["acceptance_criteria"] = ["New requirement after review"]
        c[3]["body"] = "AICTRL_TASK_JSON_BEGIN\n" + json.dumps(task) + "\nAICTRL_TASK_JSON_END"
    elif kind == "base":
        c[4]["base"]["sha"] = "c" * 40
    else:
        c[5][0]["filename"] = "src/aictrl/other.py"
    current = admit(*c, now=NOW)
    assert old["key"] == current["key"]
    with pytest.raises(ReviewRejected, match="REVIEW_INPUT_CHANGED"):
        persist_decision(current, decision(old), Store(), lambda: None, old["review_input_sha256"])


def test_store_never_sends_update_sha(monkeypatch):
    calls = []
    def api(path, method="GET", payload=None, **kwargs):
        calls.append((path, method, payload))
        return {"ref": "refs/heads/" + adapter.LEDGER_BRANCH}
    monkeypatch.setattr(adapter, "api", api)
    store = adapter.GitHubDecisionStore()
    store.create("decisions/key.json", {})
    assert calls[-1][1] == "PUT"
    assert calls[-1][2]["branch"] == adapter.LEDGER_BRANCH
    assert "sha" not in calls[-1][2]


def test_fetch_adapter_refetches_source_pr_files_and_baseline(monkeypatch):
    c = context()
    monkeypatch.setattr(adapter, "binding", lambda: c[0])
    monkeypatch.setattr(adapter, "admit", lambda *a, **kw: admit(*a, now=NOW, **kw))
    calls = []
    def api(path, *a, **kw):
        calls.append(path)
        if "issues/comments/" in path:
            return c[1]
        if path.endswith("issues/9"):
            return c[3]
        if "/comments?" in path:
            return [c[2]]
        if "/files?" in path:
            return c[5]
        if "/compare/" in path:
            return {"status": "ahead"}
        return c[4]
    monkeypatch.setattr(adapter, "api", api)
    assert adapter.fetch_bundle(11)["key"] == bundle()["key"]
    assert any("/compare/" in path for path in calls)
