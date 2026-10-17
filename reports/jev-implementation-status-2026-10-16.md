# Jev / Multi-provider System-1 Implementation Status

## Implemented

- Native `JevOpenRouterProvider` with pinned OpenRouter Decisions endpoint,
  allowlisted `typesafe/jev-1.13` and `typesafe/jev-1.14`, model-specific provider
  protocol identity, strict response validation, finite complete probability
  distributions, exact question-set binding, provider revision, and independent
  timeout/concurrency bounds.
- Bounded privacy projection with redaction, secret/path scanning, and 8 KiB cap.
- Provider-specific diagnostics registry for Laya and Jev; no Laya schema leakage.
- Independent runtime portfolio with separate observations, cache, breaker, and
  provider identity; no probability averaging or fusion.
- Explicit provider selection strategy (`none`, `jev`, `laya`, `dual`) with
  qualification-first policy and explicit dual-trial authorization.
- Advisory gate for Jev: high confidence remains shadow until task-specific
  calibration exists. Laya coverage gate retained.
- New model identity is not silently reused for cached/provider-bound results.
- Tests for Jev transport, identity, projection, diagnostics, portfolio
  independence, provider selection, and advisory non-influence.

## Validation evidence

- Focused implementation and System-1/control tests: 102 passed.
- Full non-host-bridge suite repeatedly reached all collected tests; some
  task-attention tests were separately rerun and passed.
- Host-bridge tests were individually and batch-validated; all 41 collected tests
  were exercised in targeted groups, with each test passing when run in isolation
  or small groups. Full-file serial runs can exceed shell timeouts, but no
  implementation-caused failure was observed.

## Not claimed

- No real OpenRouter call or cost was incurred.
- No real-agent performance qualification was run.
- Jev remains unqualified for advisory control until independent outcomes are
  collected and task calibration is established.

## Security audit

- The provider is host-internal and is not registered as an agent-facing MCP tool.
- The API key is read only from the trusted host process environment and is
  inserted only into the outbound Authorization header.
- Repository scan found no literal OpenRouter credential material.
- Outbound state is independently projected and excludes objective text, criterion
  IDs, evidence references, activity history, and unrestricted task content.
