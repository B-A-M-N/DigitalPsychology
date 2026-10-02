"""Operator-only DigitalPsychology telemetry and administration MCP."""
from __future__ import annotations

from typing import Any

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # transport stays optional for library consumers
    MCPServer = None

from ..service import DigitalPsychologyService


def create_admin_server(service: DigitalPsychologyService | None = None):
    if MCPServer is None:
        raise RuntimeError("MCP transport dependency is not installed")
    service = service or DigitalPsychologyService()
    server = MCPServer(
        "digital-psychology-admin",
        instructions=("Operator-only telemetry, profile validation, and experiment "
                      "administration. This interface is separate from the private "
                      "host control MCP and must not be registered for the controlled agent."),
    )

    @server.tool()
    def ingest_behavior_event(event: dict[str, Any]) -> dict[str, Any]:
        """Ingest one schema-validated, origin-scoped behavioral event."""
        return service.ingest_behavior_event(event)

    @server.tool()
    def behavioral_advice(context: dict[str, Any], eligible_routes: list[Any],
                          static_policy: dict[str, Any]) -> dict[str, Any]:
        """Return bounded advice for routes already legal under CFW policy."""
        return service.behavioral_advice(context=context, eligible_routes=eligible_routes,
                                         static_policy=static_policy)

    @server.tool()
    def validate_routing_profile(profile: dict[str, Any]) -> dict[str, Any]:
        """Validate a deployable profile and its exact scope contract."""
        return service.validate_routing_profile(profile)

    @server.tool()
    def run_registered_slow_loop(experiment_id: str, authorization: dict[str, Any]) -> dict[str, Any]:
        """Run a protected experiment record after host authorization."""
        return service.run_registered_slow_loop(experiment_id, authorization)

    return server


def main() -> None:
    service = DigitalPsychologyService()
    server = create_admin_server(service)
    try:
        server.run("stdio")
    finally:
        service.close()


__all__ = ["create_admin_server", "main"]
