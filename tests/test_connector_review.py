import json
import base64
import subprocess
import sys

import pytest

from scripts.aictrl_connector_review import ConnectorSession
from scripts import aictrl_review_wakeup as adapter
from aictrl.review_wakeup import ReviewRejected, admit
from test_review_wakeup import context, decision, NOW


@pytest.fixture
def github(monkeypatch):
    c = context()
    monkeypatch.setattr(adapter, "binding", lambda: c[0])
    monkeypatch.setattr(adapter, "admit", lambda *args, **kwargs: admit(*args, **kwargs, now=NOW))
    prefix = f"https://api.github.com/repos/{adapter.REPO}/"
    documents = {
        "issues/comments/11": c[1], "issues/9": c[3],
        "issues/9/comments?per_page=100&page=1": [c[2]], "pulls/10": c[4],
        "pulls/10/files?per_page=100&page=1": c[5],
        "compare/" + json.loads(c[3]["body"].split("AICTRL_TASK_JSON_BEGIN")[1].split("AICTRL_TASK_JSON_END")[0])["head_sha"] + "..." + "a" * 40: {"status": "ahead"},
        "git/ref/heads/" + adapter.LEDGER_BRANCH: {"ref": "refs/heads/" + adapter.LEDGER_BRANCH},
    }
    ledger, writes = {}, []

    def reply(request):
        args = request["arguments"]
        response = {"id": request["id"], "status": "ok"}
        if request["tool"] == "github_fetch":
            path = args["url"].removeprefix(prefix)
            if path.startswith("contents/"):
                name = path.split("?")[0][len("contents/"):]
                if name not in ledger:
                    return dict(response, status="not_found")
                value = {"type": "file", "encoding": "base64", "content":
                         base64.b64encode(ledger[name].encode()).decode()}
            else:
                value = documents[path]
            response["content"] = json.dumps(value)
        else:
            assert request["tool"] == "github_create_file"
            assert args["branch"] == adapter.LEDGER_BRANCH
            if args["path"] in ledger:
                return dict(response, status="error")
            ledger[args["path"]] = args["content"]
            writes.append(args)
        return response

    return c, reply, writes


def drive(session, reply):
    result = session.next()
    while result["status"] == "TOOL_REQUIRED":
        result = session.next(reply(result))
    return result


def test_connector_prepare_record_duplicate_and_lost_response(github):
    _, reply, writes = github
    prepared = drive(ConnectorSession(11), reply)
    assert prepared["status"] == "REVIEW_REQUIRED"
    b = prepared["bundle"]

    def ambiguous(request):
        response = reply(request)
        return dict(response, status="error") if request["tool"] == "github_create_file" else response

    recorded = drive(ConnectorSession(11, decision=decision(b), review_input_sha=b["review_input_sha256"]), ambiguous)
    assert recorded["status"] == "ALREADY_RECORDED"
    assert recorded["record"]["decision"] == decision(b)
    duplicate = drive(ConnectorSession(11), reply)
    assert duplicate["status"] == "ALREADY_RECORDED"
    assert len(writes) == 1


def test_connector_revalidates_head_before_any_write(github):
    c, reply, writes = github
    b = drive(ConnectorSession(11), reply)["bundle"]
    c[4]["head"]["sha"] = "b" * 40
    result = drive(ConnectorSession(11, decision=decision(b), review_input_sha=b["review_input_sha256"]), reply)
    assert result == {"status": "REJECTED", "reason": "STALE_HEAD"}
    assert not writes


def test_connector_rejects_malformed_or_denied_raw_reads(github):
    for response in ({"status": "ok", "content": '{"id":1,"id":2}'}, {"status": "error"}):
        session = ConnectorSession(11)
        request = session.next()
        assert session.next(dict(response, id=request["id"]))["status"] == "REJECTED"


def test_connector_response_cannot_cross_sessions(github):
    _, reply, _ = github
    first, second = ConnectorSession(11), ConnectorSession(11)
    a, b = first.next(), second.next()
    with pytest.raises(ReviewRejected, match="CONNECTOR_RESPONSE_MISMATCH"):
        second.next(reply(a))
    assert first.next({"id": a["id"], "status": "error"})["status"] == "REJECTED"
    assert second.next({"id": b["id"], "status": "error"})["status"] == "REJECTED"


def test_cli_keeps_one_process_and_rejects_failed_connector():
    process = subprocess.Popen([sys.executable, "-u", "-m", "scripts.aictrl_connector_review",
                                "--comment-id", "11"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        request = json.loads(process.stdout.readline())
        assert request["tool"] == "github_fetch"
        output, errors = process.communicate(json.dumps({"id": request["id"], "status": "error"}) + "\n", timeout=5)
        assert json.loads(output) == {"status": "REJECTED", "reason": "GITHUB_REQUEST_FAILED"}
        assert process.returncode == 1 and not errors
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
