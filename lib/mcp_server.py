"""Compatibility entrypoint for the DigitalPsychology admin MCP.

New deployments should configure ``digital-psychology-admin-mcp`` explicitly.
The controlled agent must not receive this operator interface or the private
``digital-psychology-control-mcp`` connection.
"""
from __future__ import annotations

from .service import DigitalPsychologyService, _canonical
from .transport.admin_mcp import create_admin_server


def create_server(service: DigitalPsychologyService | None = None):
    """Backward-compatible alias for :func:`create_admin_server`."""
    return create_admin_server(service)


def main() -> None:
    service = DigitalPsychologyService()
    server = create_admin_server(service)
    try:
        server.run("stdio")
    finally:
        service.close()


__all__ = ["DigitalPsychologyService", "_canonical", "create_server", "main"]


if __name__ == "__main__":
    main()
