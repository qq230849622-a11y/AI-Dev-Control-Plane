# AI Dev Control Plane

Minimal CTRL-001 bootstrap project.

## Usage

```text
aictrl --version
python -m aictrl --version
aictrl validate <envelope.json>
python -m aictrl validate <envelope.json>
aictrl route --registry <binding-file-or-directory> <envelope.json>
python -m aictrl route --registry <binding-file-or-directory> <envelope.json>
```

`route` validates an AICTRL envelope before binding its exact `project_key` and
`repo` to an enabled `AICTRL_PROJECT_V1` registry entry. Routing has no
fallback or default project.
