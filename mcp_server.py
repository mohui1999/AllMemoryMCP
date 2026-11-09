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
                            create_timeline_memory, delete_memory,
                            get_agent_summary, get_memory, get_memory_context,
                            initialize, list_agents, list_categories, list_memory_revisions,
                            list_threads, memory_guide as get_memory_guide,
                            post_message, resolve_thread,
                            search_memories, start_thread, update_memory,
                            upsert_agent_summary, upsert_event_memory)

mcp = FastMCP("AllMemoryMCP", host=os.getenv("MCP_HOST", "0.0.0.0"),
              port=int(os.getenv("MCP_PORT", "8001")), instructions=(
    "Shared multi-tenant memory for Agents. Every authenticated Agent may read all "
    "memories within its own tenant. Only the uploading Agent may edit or delete its "
    "memory. Memory results identify their source Agent. Negotiation messages are "
    "append-only and never directly modify memories.\n\n"
    "MEMORY MODEL — three layers (call memory_guide for the full machine-readable contract):\n"
    "1. summary: EXACTLY ONE long Markdown document per Agent. It is the overview of "
    "ALL of that Agent's memories — who it is, what it is working on, decisions, "
    "conventions and pointers to details. It can be long. Writing REPLACES the whole "
    "document; it never creates a second summary. This is the HIGHEST PRIORITY thing "
    "to read, especially when you want to know what another Agent is doing.\n"
    "2. event: the CURRENT STATE of one long-running subject, keyed by "
    "(category, event_key). Categories are free-form, e.g. development, finance, "
    "health. Versioned; revisions are kept. Use it for the detailed state behind a "
    "subject mentioned in a summary.\n"
    "3. timeline: an append-only, dated log entry with occurred_at (timezone-qualified "
    "ISO 8601). Use it to answer what happened and when. Corrections append a new row "
    "with supersedes_memory_id.\n"
    "Older memory_write records are the 'legacy' layer.\n\n"
    "RECOMMENDED READING ORDER: memory_whoami -> memory_agents (see EVERY Agent's one "
    "summary at once) -> memory_summary_get(agent_id) for the full overview of the "
    "Agent you care about -> ONLY THEN memory_search with memory_layer='event' or "
    "'timeline' for the details you actually need. memory_context(detail_level='summary') "
    "returns the overview plus ready-to-use follow-up calls. Do not start by loading "
    "all timeline/event rows.\n\n"
    "RECOMMENDED WRITING ORDER: memory_timeline_write for dated facts, "
    "memory_event_write for a subject's current state, then memory_summary_write to "
    "refresh your ONE overview of everything. Each Agent must connect with its own "
    "token: call memory_whoami first to confirm the identity behind this connection. "
    "Pass expected_version when two writers may update the same summary/event; it is "
    "optional for a single writer."
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
def memory_whoami() -> dict:
    """Return the identity (tenant, Agent id, scopes) behind this connection.

    Call this at the start of every session. If two Agents report the same
    agent_id, they are sharing one token and memory ownership will be wrong.
    Ask an administrator for a separate credential for each Agent. The response
    also includes a compact explanation of the three memory layers, so a client
    does not have to guess what summary/timeline/event mean. Call memory_guide
    for the full contract.
    """
    principal = caller()
    return {
        "tenant_id": principal.tenant_id, "agent_id": principal.agent_id,
        "scopes": list(principal.scopes),
        "memory_model": {
            "summary": "ONE long document per Agent = overview of ALL its memories. Read this FIRST.",
            "event": "current state of one subject, keyed by (category, event_key).",
            "timeline": "append-only dated log entry with occurred_at.",
            "priority": "summary > event/timeline details on demand",
        },
        "reading_order": [
            "memory_agents — see every Agent's summary at once",
            "memory_summary_get(agent_id) — the full overview of one Agent",
            "memory_search(memory_layer='event'|'timeline') — details only as needed",
        ],
        "guide_tool": "memory_guide",
    }


@mcp.tool()
def memory_guide() -> dict:
    """Explain what summary / timeline / event mean and how to read and write them.

    Call this whenever you are unsure which layer to use. Returns the full
    machine-readable contract: layer meanings (English + Chinese), cardinality,
    length limits, read/write tools, update rules, the recommended reading order
    and the writing rules. No database changes are made.
    """
    return get_memory_guide(caller())


@mcp.tool()
def memory_agents(summary_mode: str = "full", limit: int = 50) -> dict:
    """List every Agent in this tenant WITH its one summary — the best first read.

    Use this to learn what other Agents are working on before touching their
    detailed records. Each item contains the Agent's ONLY whole-memory overview
    plus counts of its timeline/event rows and the categories it uses. Then call
    memory_summary_get(agent_id) for the full text, or memory_search with
    owner_agent_id and memory_layer='event'/'timeline' for details.

    summary_mode: 'full' (default, complete summaries), 'preview' (first 600
    characters each), or 'none' (directory/counts only).
    """
    return list_agents(caller(), summary_mode, limit)


@mcp.tool()
def memory_search(query: str = "", limit: int = 20, owner_agent_id: str | None = None,
                  memory_layer: str | None = None, category: str | None = None,
                  event_key: str | None = None, occurred_from: str | None = None,
                  occurred_to: str | None = None, offset: int = 0) -> dict:
    """Search tenant-readable memories; use it for DETAILS, after reading summaries.

    Layers:
    - 'summary': the ONE long whole-memory overview per Agent (highest read
      priority; use memory_agents / memory_summary_get for the full text).
    - 'event': the current state of a subject, filter with category / event_key.
    - 'timeline': append-only dated entries, filter with occurred_from /
      occurred_to / category.
    - 'legacy': older free-form memory_write records.

    Matching summaries are ranked slightly higher. For cross-agent work, read
    memory_agents first, then filter this tool with owner_agent_id.
    """
    return {
        "items": search_memories(
            query, limit, owner_agent_id, caller(), offset, memory_layer,
            category, event_key, occurred_from, occurred_to,
        ),
        "layer_guide": {
            "summary": "ONE long whole-memory overview per Agent; read first",
            "event": "current state per (category, event_key)",
            "timeline": "append-only dated entries",
            "legacy": "older free-form records",
        },
        "hint": "Read the Agent's summary first (memory_agents / memory_summary_get); use this tool for event/timeline details.",
    }


@mcp.tool()
def memory_summary_get(agent_id: str | None = None) -> dict:
    """Read ONE Agent's single, potentially long, whole-memory overview.

    This is the highest-priority memory in the system: every Agent has exactly
    ONE active summary, a Markdown digest of ALL of its memories (what it is,
    what it is working on, decisions, conventions, pointers to details). Read it
    BEFORE that Agent's event or timeline rows. Defaults to the caller;
    memory_agents lists the summaries of all Agents at once. Returns
    item=null when the Agent has not written a summary yet.
    """
    principal = caller()
    item = get_agent_summary(principal, agent_id or principal.agent_id)
    return {
        "item": item,
        "layer_guide": {
            "layer": "summary",
            "meaning": "ONE long document per Agent = overview of ALL its memories; not a single memory entry.",
            "cardinality": "exactly one active row per (tenant_id, agent_id)",
            "next": "Use memory_search with memory_layer='event' or 'timeline' for details.",
        },
    }


@mcp.tool()
def memory_summary_write(markdown: str, source_watermark: str | None = None,
                         expected_version: int | None = None,
                         change_note: str | None = None) -> dict:
    """Create or REPLACE your ONE summary — the overview of ALL your memories.

    There is exactly one active summary per Agent, enforced by the database;
    calling this again replaces the whole document and keeps a revision
    snapshot, it never adds a second summary. Write a long, structured Markdown
    overview (up to 100000 characters): identity, current work, decisions,
    conventions, open questions and pointers to your event/timeline records.
    Other Agents read this first to understand what you are doing.

    expected_version is OPTIONAL for a single writer (the current version is
    detected automatically, still with optimistic locking). Pass it explicitly
    when several writers may update the summary concurrently; a mismatch raises
    a conflict so you can re-read and retry. Typical flow: write timeline/event
    details, then refresh this summary.
    """
    try:
        return upsert_agent_summary(
            caller(), markdown, source_watermark, expected_version, change_note,
        )
    except ScopeError as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
def memory_timeline_write(title: str, content: str, occurred_at: str,
                          category: str | None = None,
                          tags: list[str] | None = None,
                          supersedes_memory_id: str | None = None) -> dict:
    """Append a TIMELINE row: one dated fact ("what happened, and when").

    This is the time layer, not the overview and not a subject's current state.
    occurred_at must be timezone-qualified ISO 8601 (e.g. 2026-11-09T14:30:00+08:00).
    Rows are immutable: to correct one, append a new row setting
    supersedes_memory_id to the old row's id. Keep each row short and factual;
    put evolving conclusions in an event memory and the global picture in your
    single summary.
    """
    try:
        return create_timeline_memory(
            caller(), title, content, occurred_at, category, tags,
            supersedes_memory_id,
        )
    except ScopeError as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
def memory_event_write(category: str, event_key: str, title: str,
                       content_markdown: str, expected_version: int | None = None,
                       change_note: str | None = None,
                       tags: list[str] | None = None) -> dict:
    """Create/update an EVENT memory: the CURRENT STATE of one ongoing subject.

    An event memory is a living document identified by (category, event_key),
    e.g. (development, AllMemoryMCP), (finance, long-term allocation),
    (health, allergy treatment). Categories are free-form; development, finance
    and health are the conventional ones. There is ONE active row per
    (Agent, category, event_key): calling again replaces its content and keeps
    a revision. Use it for detailed, evolving context that a summary points to —
    not for a one-off dated fact (use memory_timeline_write) and not as a second
    whole-Agent overview. Pass expected_version when another writer may have
    updated the same key; memory_revisions shows its history.
    """
    try:
        return upsert_event_memory(
            caller(), category, event_key, title, content_markdown,
            expected_version, change_note, tags,
        )
    except ScopeError as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
def memory_context(agent_id: str | None = None, query: str = "",
                   category: str | None = None,
                   timeline_limit: int = 10, event_limit: int = 10,
                   detail_level: str = "full") -> dict:
    """Load one Agent's memory picture with the summary as the top priority.

    detail_level='summary' (recommended first call) returns ONLY the Agent's one
    long whole-memory overview, its categories, counts and concrete `next_calls`
    for fetching event/timeline details on demand — avoiding a flood of detail
    rows. detail_level='full' (default, backward compatible) also returns recent
    timeline rows and matching event rows.

    The response always carries `layers` (what summary/event/timeline mean),
    `reading_order` and `next_calls`. Use category to focus on one subject and
    query for hybrid keyword/vector retrieval. For a roster of ALL Agents and
    their summaries, call memory_agents instead.
    """
    return get_memory_context(
        caller(), agent_id, query, category, timeline_limit, event_limit,
        detail_level,
    )


@mcp.tool()
def memory_categories(agent_id: str | None = None) -> dict:
    """List categories used by an Agent's event and timeline memories.

    Use these category strings with memory_search(memory_layer='event',
    category=...) to drill into the details referenced by a summary.
    """
    return {"items": list_categories(caller(), agent_id)}


@mcp.tool()
def memory_revisions(memory_id: str) -> dict:
    """List version history for a summary or event memory visible in this tenant."""
    return {"items": list_memory_revisions(caller(), memory_id)}


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
                  memory_type: str | None = None, tags: list[str] | None = None,
                  expected_version: int | None = None,
                  change_note: str | None = None) -> dict:
    """Update only a memory uploaded by the authenticated Agent."""
    try:
        item = update_memory(
            caller(), memory_id, content, memory_type, tags,
            expected_version, change_note,
        )
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
