"""MCP interface layer: fast, strongly-typed remote procedure call surface."""

from backend.app.mcp.server import build_server

__all__ = ["build_server"]