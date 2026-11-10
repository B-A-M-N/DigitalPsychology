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

## Private Codex host launcher

For Codex profiles that must use DP without exposing its control protocol to the
model, use the local `codex-dp` or `harvardcodex-dp` launcher. It starts Codex
App Server with native execution and configured MCP servers disabled, while a
trusted host process calls the standard-stdio `digital-psychology-control-mcp`
through `MCPControlClient`. The model receives only the host-registered CFW
workspace actions; DP control tools are never part of `tools/list`.

The admin MCP is a separate operator interface. Do not register it in an agent
profile with an empty tool allowlist: Codex reports that as an unknown MCP with
zero tools, and it does not connect DP to the host lifecycle. Generic MCP hosts
can launch the control server as a private child and implement the same host
boundary without Codex App Server. App Server is the Codex-specific embedding
transport, not a requirement of MCP itself.

Launch from a workspace with `codex-dp --workspace /path/to/workspace -- "task text"` or `harvardcodex-dp --workspace /path/to/workspace -- "task text"`. HarvardCodex delegates credential loading to its existing launcher; the DP host receives the credential only for the App Server provider connection.
The host uses bounded workspace read/inspect/write actions, blocks sensitive
path components, and keeps provider credentials out of the private DP child and
model-visible action results.
