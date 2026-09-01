# Changelog

All notable changes to AI Dev Control Plane are documented here.

## [Unreleased]

- Prepare the controller for public open-source use.
- Document the AICTRL V1 envelope and project-binding contracts.
- Add contributor, security, and code-of-conduct guidance.

## [0.1.0] - Initial development baseline

- Deterministic validation for AICTRL task, event, decision, and result envelopes.
- Explicit project-key and repository binding with fail-closed routing.
- Bounded GitHub issue-comment dispatch for controller-authored tasks.
- Scope, head-SHA, pull-request, test, and worker-session checks.
- Windows self-hosted runner integration for Agent Orchestrator workflows.
- Focused tests for malformed input, identity mismatch, stale state, and failure paths.
