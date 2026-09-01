# Changelog

All notable changes to AI Dev Control Plane are documented here.

## [Unreleased]

No unreleased changes yet.

## [0.1.0] - 2026-09-01

Initial public OSS readiness release:

- Deterministic validation for AICTRL task, event, decision, and result envelopes.
- Explicit project-key and repository binding with fail-closed routing.
- Bounded GitHub issue-comment dispatch for controller-authored tasks.
- Scope, head-SHA, pull-request, test, and worker-session checks.
- Windows self-hosted runner integration for Agent Orchestrator workflows.
- Public CI across Python 3.9, 3.11, and 3.13.
- Sanitized protocol examples and contributor/security documentation.
- Focused tests for malformed input, identity mismatch, stale state, and failure paths.
