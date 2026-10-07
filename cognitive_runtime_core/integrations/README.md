# Harness integrations

Harness integrations translate a harness's public event and result surface
into `cognitive_runtime_core` contracts. Application integrations connect
neutral lifecycle events to injected cognitive-system ports. Harness names,
event taxonomies, and capability limits stay in harness adapters; core
contracts and cognitive application cores do not import those adapters.

The Athena adapter is observational because Athena's public API does not
provide action blocking or completion interception. The Codex App Server
transport adapter is distributed separately as
`cognitive-runtime-codex-adapter`; the neutral core does not import it. The
generic Codex adapter translates only harness boundaries. Its separately
selected `codex.cfw_host` subpackage binds those boundaries to CFW's task,
action, validator, and completion contracts under an explicit CFW extra; it is
not loaded by the neutral core or generic Codex adapter. The
reference adapter accepts only canonical lifecycle events and is a contract
conformance ingress, not evidence of a production harness integration. The
CFW integration consumes canonical events and injected CFW/CSW authority
context; it has no dependency on Codex protocol names. Athena records the
structural task-port contract version and remains unverified until the
connected host supplies separate attestation.

The triad integration coordinates reversible application and prepared-decision
recovery across structural CFW and CSW authority ports. DP supplies its
behavior projection and lease semantics through callbacks, then owns the DP
commit or abort receipt; the coordinator imports no cognitive application or
harness implementation. The generic Codex adapter remains a boundary adapter;
the optional CFW host integration supplies the product-specific task binding.
