"""Temporary fail-closed bootstrap for AO v0.12.10 project discovery.

AO v0.12.10 `project ls --json` returns project summaries without repo/default
branch fields.  The production dispatcher originally tried to match those
missing fields.  This bootstrap enumerates summary IDs, resolves each project
with `project get`, verifies exact repo/default-branch identity, then delegates
to the normal dispatcher.  PRE-005 must fold this into the dispatcher and
remove this file before acceptance.
"""

import aictrl_task_dispatch as dispatch


def find_ao_project_v01210(binary, binding):
    document = dispatch.command_json(binary, "project", "ls", "--json")
    summaries = document.get("projects") if isinstance(document, dict) else None
    if not isinstance(summaries, list):
        raise dispatch.DispatchFailure("AO_PROJECT_LOOKUP_FAILED")

    expected_url = dispatch.normalized_repo_url(f"https://github.com/{binding.repo}.git")
    matches = []
    for summary in summaries:
        project_id = summary.get("id") if isinstance(summary, dict) else None
        if not isinstance(project_id, str) or not project_id.strip():
            continue
        project_document = dispatch.command_json(binary, "project", "get", project_id, "--json")
        project = project_document.get("project") if isinstance(project_document, dict) else None
        if not isinstance(project, dict):
            continue
        config = project.get("config") if isinstance(project.get("config"), dict) else {}
        configured_branch = project.get("defaultBranch", config.get("defaultBranch"))
        if (
            dispatch.normalized_repo_url(project.get("repo")) == expected_url
            and configured_branch == binding.default_branch
        ):
            matches.append(project)

    if len(matches) != 1:
        raise dispatch.DispatchFailure("AO_PROJECT_IDENTITY_MISMATCH")
    return matches[0]


dispatch.find_ao_project = find_ao_project_v01210


if __name__ == "__main__":
    raise SystemExit(dispatch.main())
