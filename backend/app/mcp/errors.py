"""Structured MCP tool errors.

Every failure that can happen inside an MCP tool is raised as a ``ToolError``
whose message is a compact JSON object::

    {"code": "CASE_NOT_FOUND", "message": "No case or dataset directory ...",
     "details": {"case_id": "bench-999"}}

A client can read the human message and / or parse the payload. Entity-level
failures use ``ToolError`` (the model sees and can correct them); unexpected
exceptions are wrapped with an internal marker so no stray stack trace ever
reaches the MCP client.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError


class Code:
    """Structured error codes exposed to MCP clients."""

    CASE_NOT_FOUND = "CASE_NOT_FOUND"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    REQUIREMENT_NOT_FOUND = "REQUIREMENT_NOT_FOUND"
    SUPPLIER_NOT_FOUND = "SUPPLIER_NOT_FOUND"
    SUPPLIER_NOT_IN_CASE = "SUPPLIER_NOT_IN_CASE"
    SUPPLIER_NOT_EVALUATED = "SUPPLIER_NOT_EVALUATED"
    ANALYSIS_REQUIRED = "ANALYSIS_REQUIRED"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    NO_DECISION = "NO_DECISION"
    NOT_ANALYZED = "NOT_ANALYZED"
    RETRIEVAL_FAILED = "RETRIEVAL_FAILED"
    INVALID_PARAM = "INVALID_PARAM"
    INTERNAL = "INTERNAL_ERROR"


def mcp_error(code: str, message: str, details: dict[str, Any] | None = None) -> ToolError:
    """Build a ToolError carrying a structured JSON message."""
    payload = {"code": code, "message": message, "details": details or {}}
    return ToolError(json.dumps(payload, default=str, sort_keys=True))


def internal_error(exc: Exception) -> ToolError:
    """Wrap an unexpected exception without leaking internals to the client."""
    return mcp_error(
        Code.INTERNAL,
        "Unexpected internal error while executing the tool. See server logs for details.",
        {"error_type": type(exc).__name__},
    )