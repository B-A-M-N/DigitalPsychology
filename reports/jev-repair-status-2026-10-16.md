# Jev Integration Repair — Current Status

## Corrected

- OpenRouter documented response parsing for Jev 1.13 and 1.14, including served
  snapshot allowlists, answer `type`, `choice`, probabilities, and `confidence`.
- Separate requested/served model identities and provider revision recording.
- HTTP failure classification for invalid requests, credentials, throttling, quota,
  and server outages.
- Singular and plural service construction without recursion or environment mutation;
  singular Jev and Laya now share one explicit parse path.
- Canonical `CalibrationBundle.from_mapping()` loading in the portfolio path.
- Broadcast lifecycle handling for `reset_identity`, `resync`, and `close`.
- Portfolio decision-basis validation, stale-revision rejection, independent partial
  provider failure recording, and an optional bounded portfolio deadline.
- Provider selection now enforces remote suitability on every path, local-only data
  constraints, explicit dual-trial authorization, and provider availability.
- Stable opaque criterion slot mapping shared across the projection.
- Question-family-specific Jev projection with objective, unfinished criteria, activity,
  before/after criteria, approach comparison, and eligible workflow evidence as relevant.
- Advisory promotion path: qualified Jev with active calibration and clear drift can be
  `ok`; unqualified family, drifted calibration, or changed served snapshot cannot.
- Portfolio observations are preserved in the prepared-tick basis and converted to
  authenticated System-1 decision events during commit.

## Validation evidence

- Focused regression suite: 126 passed, 3 optional Laya skips.
- Control-tick/event/traceability suite: 42 passed.
- Full-file and compile checks pass.

## Not claimed

- No live OpenRouter call or real-agent performance trial was executed.
- The repair is a shadow-qualified foundation; advisory activation still requires
  independent outcomes and operator-controlled qualification.
