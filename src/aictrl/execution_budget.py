"""Goal guidance and controller-owned, invocation-local test evidence.

No worker evidence or on-disk cache is accepted as authority.
"""

import hashlib
import json
import os
import time
from pathlib import Path


def budget_for(task):
    policy = task.get("execution_budget_policy", {})
    return {
        "version": 1,
        "worker_seconds": policy.get("worker_seconds", 1200),
        "self_repair_rounds": policy.get("self_repair_rounds", 3),
        "reuse_seconds": policy.get("reuse_seconds", 300),
        "reusable_commands": policy.get("reusable_commands", []),
    }


def worker_guidance(task):
    budget = budget_for(task)
    return (
        "Execution budget: deliver one complete outcome with acceptance evidence; "
        "batch related implementation, tests and fixes inside the declared scope. "
        "Do not pause for line/file counts or internal subtask completion. "
        f"Within this one session ({budget['worker_seconds']}s), self-repair ordinary "
        f"failures for up to {budget['self_repair_rounds']} focused rounds per unresolved "
        "failure; each round needs a changed hypothesis/input. Stop a stalled loop. "
        "Use targeted checks after meaningful batches or errors. Reuse successful local "
        "checks only with unchanged inputs/environment; do not retry identical failures. "
        "Leave the canonical full suite to the controller gate; required project checks "
        "and material shared-infrastructure changes override this saving. Stop for scope, "
        "identity or authority conflicts, missing prerequisites, destructive/production "
        "actions, systemic failures or an explicit human gate. Never expand authorization."
    )


def input_fingerprint(workspace, task, head, deadline=None):
    """Conservatively hash ALL workspace files, including ignored dependencies.

    Reuse is opt-in for hermetic commands. External services, mutable tools outside
    this workspace and nondeterministic checks must never be marked reusable.
    Symlinks/junctions and unreadable inputs disable reuse instead of omitting data.
    Git metadata is excluded; the independently read HEAD is bound separately.
    """
    root = Path(workspace).resolve()
    digest = hashlib.sha256()
    context = {"workspace": str(root), "task": task, "head": head,
               "environment": dict(os.environ)}
    digest.update(json.dumps(context, sort_keys=True).encode())
    def unreadable(error):
        raise error

    try:
        root_stat = root.stat()
        digest.update(json.dumps([".", root_stat.st_mode, root_stat.st_mtime_ns]).encode())
        for directory, dirs, files in os.walk(root, followlinks=False,
                                               onerror=unreadable):
            if deadline is not None and time.monotonic() >= deadline:
                return None
            if Path(directory) == root:
                dirs[:] = [name for name in dirs if name != ".git"]
                files = [name for name in files if name != ".git"]
            dirs.sort()
            for name in dirs + sorted(files):
                if deadline is not None and time.monotonic() >= deadline:
                    return None
                path = Path(directory) / name
                stat = path.lstat()
                if path.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                    return None
                content = hashlib.sha256()
                if path.is_file():
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            if deadline is not None and time.monotonic() >= deadline:
                                return None
                            content.update(block)
                    after = path.stat()
                    if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                        return None
                digest.update(json.dumps([str(path.relative_to(root)), stat.st_mode, stat.st_mtime_ns,
                                          path.is_file(), content.hexdigest()]).encode())
    except OSError:
        return None
    return digest.hexdigest()


class GateEvidence:
    """Private successful evidence for a single controller gate invocation."""

    def __init__(self, max_age):
        self.max_age = max_age
        self._successes = {}

    def reusable(self, command, fingerprint, now):
        completed = self._successes.get((command, fingerprint))
        return (fingerprint is not None and completed is not None
                and 0 <= now - completed <= self.max_age and self.max_age > 0)

    def record_success(self, command, before, after, now):
        if before is not None and before == after:
            self._successes[(command, before)] = now
