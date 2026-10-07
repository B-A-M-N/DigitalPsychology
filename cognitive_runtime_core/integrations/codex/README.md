# Codex App Server adapter

This is an optional harness adapter distribution for Codex App Server. It
contains the App Server protocol helpers, stdio client, launch profiles,
readiness checks, and native-feature diagnostics. The neutral runtime core
does not import or install it by default.

`CodexWireMethod` owns the JSON-RPC method names and `resolve_codex_home`
owns the profile-directory rule. Application integrations consume these
helpers instead of defining App Server wire methods or profile paths locally.

The adapter depends on `cognitive-runtime-core` for the shared package
namespace. CFW's host integration maps its transport surface to shared runtime
contracts. A host selects and installs this adapter explicitly; DP, CFW, and
CSW remain usable without Codex.

`CodexHarnessAdapter` projects task, model, action, validation, completion, and
task-end boundaries into the shared event contracts. It retains no raw tool
arguments in those events; action parameters are represented by a digest. It
reports the connected application's authorization or rejection of each
proposed action, then reports the host-produced action result.
`identity_for_client` pins the inspected native-feature catalog and only marks
capabilities effective when host policy and the host version are verified.
Unverified clients produce explicit degraded boundaries.

The `cfw_host` subpackage contains the optional CFW task and App Server host
adapter. It is included in this adapter distribution but imported only when a
CFW host selects it; install the adapter's `[cfw]` extra for the CFW runtime
dependency. The generic runtime and `CodexHarnessAdapter` do not import CFW.
CFW's former `scripts/codex_host` modules are compatibility exports only.

The separate DP/CFW lifecycle integration consumes canonical shared events and
injected authority context. The CFW host adapter uses CFW's local action and
completion authorities while translating Codex App Server boundaries into the
neutral contract; it does not define DP behavior policy or workflow truth.
