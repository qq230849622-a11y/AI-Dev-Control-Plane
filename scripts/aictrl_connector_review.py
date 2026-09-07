"""In-memory RPC bridge for a trusted Python runtime and GitHub connector.

The host forwards emitted tool arguments verbatim and returns raw REST text.
No credentials, gh CLI, model calls, polling or candidate code execution.
Sessions are ephemeral: loss of Python state requires fresh admission.
"""

import base64
import argparse
import json
import queue
import re
import threading
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from scripts import aictrl_review_wakeup as adapter
from aictrl.review_wakeup import (ReviewRejected, persist_decision, require,
                                  strict_json, verify_record)


def decode_fetch_response(path, text):
    """Normalize only decoded ledger Contents bodies, never their authority."""
    data = strict_json(text)
    url = urlsplit(path)
    ledger_path = rf"repos/{re.escape(adapter.REPO)}/contents/decisions/[0-9a-f]{{64}}\.json"
    if (re.fullmatch(ledger_path, url.path) and not url.fragment
            and parse_qsl(url.query, keep_blank_values=True) == [("ref", adapter.LEDGER_BRANCH)]):
        # Preserve even malformed envelope-like responses for the existing
        # ledger gate to reject. Never reinterpret them as decoded records.
        if not isinstance(data, dict) or not {"type", "encoding", "content"}.intersection(data):
            return {"type": "file", "encoding": "base64",
                    "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
    return data


class ConnectorSession:
    """Call next() once, then next(response) for each emitted connector request.

    Responses: {id, status: 'ok', content: RAW_REST_TEXT} for fetch;
    {id, status: 'ok'} for create; {id, status: 'error'} on tool failure.
    Only an explicit HTTP 404 may use status='not_found'. Never infer absence
    from empty, denied, truncated, or unparseable connector responses.
    """

    def __init__(self, comment_id, *, probe_pr=None, decision=None,
                 review_input_sha=None, timeout=300):
        self._out = queue.Queue()
        self._in = queue.Queue()
        self._pending = None
        self._started = False
        self._done = False
        self._timeout = timeout
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        args=(comment_id, probe_pr, decision, review_input_sha))

    def _request(self, path, method="GET", payload=None, missing_ok=False):
        require(path.startswith(f"repos/{adapter.REPO}/"), "API_REPO_MISMATCH")
        identifier = uuid.uuid4().hex
        if method == "GET":
            require(payload is None, "CONNECTOR_REQUEST_INVALID")
            tool, args = "github_fetch", {"url": "https://api.github.com/" + path}
        else:
            prefix = f"repos/{adapter.REPO}/contents/"
            require(method == "PUT" and path.startswith(prefix), "CONNECTOR_WRITE_FORBIDDEN")
            filename = path[len(prefix):]
            require(re.fullmatch(r"decisions/[0-9a-f]{64}\.json", filename), "CONNECTOR_WRITE_FORBIDDEN")
            require(isinstance(payload, dict) and set(payload) == {"branch", "message", "content"}
                    and payload["branch"] == adapter.LEDGER_BRANCH, "CONNECTOR_WRITE_FORBIDDEN")
            content = base64.b64decode(payload["content"], validate=True).decode("utf-8")
            strict_json(content)
            tool, args = "github_create_file", {
                "repository_full_name": adapter.REPO, "branch": adapter.LEDGER_BRANCH,
                "path": filename, "message": payload["message"], "content": content}
        self._out.put({"status": "TOOL_REQUIRED", "id": identifier, "tool": tool, "arguments": args,
                       "expires_at": time.time() + self._timeout})
        try:
            reply = self._in.get(timeout=self._timeout)
        except queue.Empty as error:
            raise ReviewRejected("CONNECTOR_RESPONSE_TIMEOUT") from error
        require(isinstance(reply, dict) and reply.get("id") == identifier, "CONNECTOR_RESPONSE_MISMATCH")
        if reply.get("status") == "not_found" and missing_ok and method == "GET":
            return None
        require(reply.get("status") == "ok", "GITHUB_REQUEST_FAILED")
        return decode_fetch_response(path, reply.get("content")) if method == "GET" else None

    def _run(self, comment_id, probe_pr, decision, review_input_sha):
        token = adapter.TRANSPORT.set(self._request)
        try:
            bundle = adapter.fetch_bundle(comment_id, probe_pr)
            store = adapter.GitHubDecisionStore()
            if decision is None:
                existing = store.read(f"decisions/{bundle['key']}.json")
                result = ({"status": "ALREADY_RECORDED", "record": verify_record(bundle, existing)}
                          if existing is not None else {"status": "REVIEW_REQUIRED", "bundle": bundle})
            else:
                def revalidate():
                    require(adapter.fetch_bundle(comment_id, probe_pr) == bundle, "REVIEW_INPUT_CHANGED")
                record, created = persist_decision(bundle, decision, store, revalidate, review_input_sha)
                result = {"status": "RECORDED" if created else "ALREADY_RECORDED", "record": record}
            self._out.put(result)
        except Exception as error:
            self._out.put({"status": "REJECTED", "reason": str(error)
                           if isinstance(error, ReviewRejected) else "REVIEW_RUNTIME_FAILED"})
        finally:
            adapter.TRANSPORT.reset(token)

    def next(self, response=None):
        require(not self._done, "CONNECTOR_SESSION_FINISHED")
        if not self._started:
            require(response is None, "CONNECTOR_UNEXPECTED_RESPONSE")
            self._started = True
            self._thread.start()
        elif self._pending is not None:
            require(isinstance(response, dict) and response.get("id") == self._pending,
                    "CONNECTOR_RESPONSE_MISMATCH")
            self._in.put(response)
            self._pending = None
        else:
            require(response is None, "CONNECTOR_UNEXPECTED_RESPONSE")
        try:
            result = self._out.get(timeout=min(self._timeout, 30))
        except queue.Empty:
            # Observation timeout is not execution failure; retain this session.
            return {"status": "RUNNING"}
        if result["status"] == "TOOL_REQUIRED":
            self._pending = result["id"]
        else:
            self._done = True
        return result


def main():
    """JSON-line RPC over one live process; never resume by replaying reads."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comment-id", type=int, required=True)
    parser.add_argument("--probe-pr", type=int)
    parser.add_argument("--decision-file")
    parser.add_argument("--review-input-sha")
    args = parser.parse_args()
    decision = strict_json(Path(args.decision_file).read_text(encoding="utf-8")) if args.decision_file else None
    session = ConnectorSession(args.comment_id, probe_pr=args.probe_pr,
                               decision=decision, review_input_sha=args.review_input_sha)
    result = session.next()
    while True:
        print(json.dumps(result), flush=True)
        if result["status"] == "RUNNING":
            result = session.next()
        elif result["status"] == "TOOL_REQUIRED":
            incoming = queue.Queue()
            threading.Thread(target=lambda: incoming.put(sys.stdin.readline()), daemon=True).start()
            try:
                line = incoming.get(timeout=max(0, result["expires_at"] - time.time()))
            except queue.Empty:
                print(json.dumps({"status": "REJECTED", "reason": "CONNECTOR_RESPONSE_TIMEOUT"}), flush=True)
                return 1
            if not line:
                return 1
            try:
                result = session.next(strict_json(line))
            except ReviewRejected as error:
                print(json.dumps({"status": "REJECTED", "reason": str(error)}), flush=True)
                return 1
        else:
            return 1 if result["status"] == "REJECTED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
