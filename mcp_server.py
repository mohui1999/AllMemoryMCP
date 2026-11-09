"""MCP tool surface; can run as remote Streamable HTTP or local stdio bridge."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.responses import JSONResponse

from memory_service import (Principal, ScopeError, authenticate, create_memory,
                            delete_memory, get_memory, initialize, list_threads,
                            post_message, resolve_thread, search_memories,
                            start_thread, update_memory)

mcp = FastMCP("AllMemoryMCP", host=os.getenv("MCP_HOST", "0.0.0.0"),
              port=int(os.getenv("MCP_PORT", "8001")), instructions=(
    "Shared multi-tenant memory for Agents. Every authenticated Agent may read all "
    "memories within its own tenant. Only the uploading Agent may edit or delete its "
    "memory. Memory results identify their source Agent. Negotiation messages are "
    "append-only and never directly modify memories."
))

# Holds the authenticated Principal for the current HTTP request (remote transport).
_request_agent: ContextVar[Principal | None] = ContextVar("request_agent", default=None)


def caller() -> Principal:
    # Remote HTTP identity comes from validated Authorization headers (a Principal).
    # For stdio, the bridge process is provisioned for exactly one Agent via
    # MCP_AGENT_ID (+ optional MCP_TENANT_ID, default "default").
    principal = _request_agent.get()
    if principal:
        return principal
    agent_id = os.getenv("MCP_AGENT_ID")
    if not agent_id and os.getenv("MCP_TRANSPORT", "streamable-http") == "stdio":
        raise ValueError("Set MCP_AGENT_ID for the local stdio bridge")
    if not agent_id:
        raise ValueError("Missing authenticated Agent identity")
    return Principal(tenant_id=os.getenv("MCP_TENANT_ID", "default"), agent_id=agent_id)


class AgentAuthMiddleware:
    """Small ASGI bearer-token gate; tool schemas never contain credentials."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode("latin1")
        scheme, _, token = auth.partition(" ")
        principal = authenticate(token) if scheme.lower() == "bearer" else None
        if not principal:
            response = JSONResponse({"error": "valid Bearer token required"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
            await response(scope, receive, send)
            return
        reset = _request_agent.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_agent.reset(reset)


@mcp.tool()
def memory_search(query: str = "", limit: int = 20, owner_agent_id: str | None = None) -> dict:
    """Search tenant-readable memories; each result includes its source Agent."""
    return {"items": search_memories(query, limit, owner_agent_id, caller())}


@mcp.tool()
def memory_get(memory_id: str) -> dict:
    """Read a memory and its uploader metadata (tenant-scoped)."""
    item = get_memory(memory_id, caller())
    if not item:
        raise ValueError("Memory not found")
    return item


@mcp.tool()
def memory_write(content: str, memory_type: str = "fact", tags: list[str] | None = None) -> dict:
    """Create a memory owned by the authenticated Agent within its tenant."""
    try:
        return create_memory(caller(), content, memory_type, tags)
    except ScopeError as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
def memory_update(memory_id: str, content: str | None = None,
                  memory_type: str | None = None, tags: list[str] | None = None) -> dict:
    """Update only a memory uploaded by the authenticated Agent."""
    try:
        item = update_memory(caller(), memory_id, content, memory_type, tags)
    except ScopeError as exc:
        raise ValueError(str(exc)) from exc
    if not item:
        raise ValueError("Memory not found or not owned by this Agent")
    return item


@mcp.tool()
def memory_delete(memory_id: str) -> dict:
    """Soft-delete only a memory uploaded by the authenticated Agent."""
    try:
        ok = delete_memory(caller(), memory_id)
    except ScopeError as exc:
        raise ValueError(str(exc)) from exc
    if not ok:
        raise ValueError("Memory not found or not owned by this Agent")
    return {"deleted": True, "memory_id": memory_id}


@mcp.tool()
def negotiation_start(subject: str, first_message: str, channel: str = "direct",
                      recipients: list[str] | None = None,
                      related_memory_ids: list[str] | None = None) -> dict:
    """Start a direct negotiation or a global Agent discussion within the tenant."""
    principal = caller()
    recipients = recipients or []
    if channel not in ("direct", "global") or (channel == "direct" and not recipients):
        raise ValueError("Use channel='global', or channel='direct' with at least one recipient")
    try:
        thread = start_thread(principal, subject, channel, recipients, related_memory_ids or [])
        post_message(principal, thread["id"], first_message)
    except ScopeError as exc:
        raise ValueError(str(exc)) from exc
    return next((t for t in list_threads(principal) if t["id"] == thread["id"]), thread)


@mcp.tool()
def negotiation_list(limit: int = 50) -> dict:
    """List global discussions and direct discussions addressed to this Agent."""
    return {"items": list_threads(caller(), limit)}


@mcp.tool()
def negotiation_post(thread_id: str, content: str, reply_to_message_id: str | None = None) -> dict:
    """Append a message to a visible, non-closed negotiation thread."""
    try:
        message = post_message(caller(), thread_id, content, reply_to_message_id)
    except (ValueError, ScopeError) as exc:
        raise ValueError(str(exc)) from exc
    if not message:
        raise ValueError("Negotiation not found or not visible to this Agent")
    return message


@mcp.tool()
def negotiation_resolve(thread_id: str, status: str = "resolved") -> dict:
    """Mark a thread resolved or closed; this never changes related memories."""
    try:
        thread = resolve_thread(caller(), thread_id, status)
    except ScopeError as exc:
        raise ValueError(str(exc)) from exc
    if not thread:
        raise ValueError("Only the thread creator may resolve it; status must be resolved or closed")
    return thread


if __name__ == "__main__":
    initialize()
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        if not os.getenv("MCP_AGENT_ID"):
            raise RuntimeError("Set MCP_AGENT_ID to bind this local bridge to one Agent")
        mcp.run(transport="stdio")
    elif transport == "streamable-http":
        import uvicorn

        @asynccontextmanager
        async def lifespan(_app):
            async with mcp.session_manager.run():
                yield

        app = Starlette(routes=[Mount("/", app=mcp.streamable_http_app())], lifespan=lifespan)
        app = AgentAuthMiddleware(app)
        uvicorn.run(app, host=os.getenv("MCP_HOST", "0.0.0.0"), port=int(os.getenv("MCP_PORT", "8001")))
    else:
        raise RuntimeError("MCP_TRANSPORT must be 'streamable-http' or 'stdio'")
