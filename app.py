from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from memory_service import (
    InvalidInput, Principal, ScopeError, VersionConflict, authenticate, create_memory,
    create_timeline_memory, delete_memory, get_agent_summary, get_memory,
    get_memory_context, get_thread, initialize, list_categories,
    list_memory_revisions, list_threads, post_message, reindex_memory_chunks,
    resolve_thread, search_memories, start_thread, update_memory,
    update_event_memory, upsert_agent_summary, upsert_event_memory,
)
from credentials import (
    authenticate_admin, issue_credential, list_credentials, revoke_credential,
    rotate_credential, verify_token,
)

app = FastAPI(title="AllMemoryMCP API", version="0.4.0",
              description="Global cross-agent memory and negotiation API (multi-tenant)")
WEB_DIR = Path(__file__).parent / "web"
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="viewer-assets")


@app.exception_handler(ScopeError)
async def scope_error_handler(request, exc: ScopeError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(VersionConflict)
async def version_conflict_handler(request, exc: VersionConflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(InvalidInput)
async def invalid_input_handler(request, exc: InvalidInput):
    # Only explicit validation errors become 422. Unexpected ValueError raised
    # inside the service must surface as 500 instead of being hidden as bad input.
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.on_event("startup")
def startup() -> None:
    initialize()


def current_principal(authorization: Annotated[str | None, Header()] = None) -> Principal:
    scheme, _, token = (authorization or "").partition(" ")
    principal = authenticate(token if scheme.lower() == "bearer" else None)
    if not principal:
        raise HTTPException(status_code=401, detail="Provide a valid Agent bearer token")
    return principal


class MemoryWrite(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    memory_type: str = Field(default="fact", max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=30)


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=20000)
    memory_type: str | None = Field(default=None, max_length=64)
    tags: list[str] | None = Field(default=None, max_length=30)
    expected_version: int | None = Field(default=None, ge=1)
    change_note: str | None = Field(default=None, max_length=1000)


class SummaryWrite(BaseModel):
    markdown: str = Field(min_length=1, max_length=100000)
    source_watermark: str | None = Field(default=None, max_length=40)
    expected_version: int | None = Field(default=None, ge=0)
    change_note: str | None = Field(default=None, max_length=1000)


class TimelineWrite(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=20000)
    occurred_at: str = Field(min_length=1, max_length=40)
    category: str | None = Field(default=None, max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=30)
    supersedes_memory_id: str | None = Field(default=None, max_length=36)


class EventWrite(BaseModel):
    category: str = Field(min_length=1, max_length=128)
    event_key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    content_markdown: str = Field(min_length=1, max_length=50000)
    expected_version: int | None = Field(default=None, ge=0)
    change_note: str | None = Field(default=None, max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=30)


class EventUpdate(BaseModel):
    content_markdown: str | None = Field(default=None, min_length=1, max_length=50000)
    tags: list[str] | None = Field(default=None, max_length=30)
    expected_version: int | None = Field(default=None, ge=1)
    change_note: str | None = Field(default=None, max_length=1000)


class ThreadStart(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    channel: str = "direct"
    recipients: list[str] = Field(default_factory=list, max_length=100)
    related_memory_ids: list[str] = Field(default_factory=list, max_length=50)
    first_message: str = Field(min_length=1, max_length=10000)


class MessageWrite(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    reply_to_message_id: str | None = None


class ResolveThread(BaseModel):
    status: str


class ViewerLogin(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    credential: str = Field(min_length=20, max_length=512)


@app.get("/", include_in_schema=False)
def viewer_page():
    return FileResponse(WEB_DIR / "index.html")


@app.post("/viewer/login")
def viewer_login(body: ViewerLogin):
    """Accept only a managed, read-only credential for the supplied username."""
    principal = verify_token(body.credential)
    if (not principal or principal.agent_id != body.username or "read" not in principal.scopes
            or "write" in principal.scopes or "admin" in principal.scopes):
        raise HTTPException(status_code=401, detail="Invalid username or read-only credential")
    return {"username": principal.agent_id, "tenant_id": principal.tenant_id}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/memories")
def memories(q: str = "", limit: int = Query(default=20, ge=1, le=100),
             owner_agent_id: str | None = None,
             offset: int = Query(default=0, ge=0),
             memory_layer: str | None = None,
             category: str | None = None,
             event_key: str | None = None,
             occurred_from: str | None = None,
             occurred_to: str | None = None,
             principal: Principal = Depends(current_principal)):
    # All authenticated agents in the same tenant can read all memories; ownership
    # is source metadata, not a visibility filter. Cross-tenant rows are excluded.
    return {"items": search_memories(
        q, limit, owner_agent_id, principal, offset, memory_layer,
        category, event_key, occurred_from, occurred_to,
    )}


@app.get("/v1/memory-summary")
def memory_summary(principal: Principal = Depends(current_principal)):
    return get_agent_summary(principal, principal.agent_id)


@app.put("/v1/memory-summary")
def memory_summary_write(body: SummaryWrite,
                         principal: Principal = Depends(current_principal)):
    return upsert_agent_summary(
        principal, body.markdown, body.source_watermark,
        body.expected_version, body.change_note,
    )


@app.get("/v1/memory-summaries/{agent_id}")
def memory_summary_read(agent_id: str, principal: Principal = Depends(current_principal)):
    return get_agent_summary(principal, agent_id)


@app.get("/v1/timeline-memories")
def timeline_list(q: str = "", limit: int = Query(default=50, ge=1, le=100),
                  offset: int = Query(default=0, ge=0), owner_agent_id: str | None = None,
                  category: str | None = None, occurred_from: str | None = None,
                  occurred_to: str | None = None,
                  principal: Principal = Depends(current_principal)):
    items = search_memories(
        q, limit, owner_agent_id, principal, offset, "timeline",
        category, None, occurred_from, occurred_to,
    )
    return {"items": items}


@app.post("/v1/timeline-memories", status_code=201)
def timeline_write(body: TimelineWrite, principal: Principal = Depends(current_principal)):
    return create_timeline_memory(
        principal, body.title, body.content, body.occurred_at,
        body.category, body.tags, body.supersedes_memory_id,
    )


@app.get("/v1/event-memories")
def event_list(q: str = "", limit: int = Query(default=50, ge=1, le=100),
              offset: int = Query(default=0, ge=0), owner_agent_id: str | None = None,
              category: str | None = None, event_key: str | None = None,
              principal: Principal = Depends(current_principal)):
    return {"items": search_memories(
        q, limit, owner_agent_id, principal, offset, "event", category, event_key,
    )}


@app.put("/v1/event-memories")
def event_write(body: EventWrite, principal: Principal = Depends(current_principal)):
    return upsert_event_memory(
        principal, body.category, body.event_key, body.title,
        body.content_markdown, body.expected_version, body.change_note, body.tags,
    )


@app.patch("/v1/event-memories/{memory_id}")
def event_edit(memory_id: str, body: EventUpdate,
               principal: Principal = Depends(current_principal)):
    result = update_event_memory(
        principal, memory_id, body.content_markdown, body.tags,
        body.expected_version, body.change_note,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Event memory not found or not owned by this Agent")
    return result


@app.get("/v1/memories/{memory_id}/revisions")
def memory_revisions(memory_id: str, principal: Principal = Depends(current_principal)):
    return {"items": list_memory_revisions(principal, memory_id)}


@app.get("/v1/categories")
def categories(agent_id: str | None = None,
               principal: Principal = Depends(current_principal)):
    """Categories in use for timeline/event memories (development, finance, health, ...)."""
    return {"items": list_categories(principal, agent_id)}


@app.get("/v1/memory-context")
def memory_context(agent_id: str | None = None, query: str = "",
                   category: str | None = None,
                   timeline_limit: int = Query(default=10, ge=0, le=50),
                   event_limit: int = Query(default=10, ge=0, le=50),
                   principal: Principal = Depends(current_principal)):
    """One call to load an Agent's summary, timeline, event memories and categories."""
    return get_memory_context(
        principal, agent_id, query, category, timeline_limit, event_limit,
    )


@app.post("/v1/memories", status_code=201)
def memory_create(body: MemoryWrite, principal: Principal = Depends(current_principal)):
    return create_memory(principal, body.content, body.memory_type, body.tags)


@app.get("/v1/memories/{memory_id}")
def memory_read(memory_id: str, principal: Principal = Depends(current_principal)):
    item = get_memory(memory_id, principal)
    if not item:
        raise HTTPException(status_code=404, detail="Memory not found")
    return item


@app.patch("/v1/memories/{memory_id}")
def memory_edit(memory_id: str, body: MemoryUpdate,
                principal: Principal = Depends(current_principal)):
    item = update_memory(
        principal, memory_id, body.content, body.memory_type, body.tags,
        body.expected_version, body.change_note,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Memory not found or not owned by this Agent")
    return item


@app.delete("/v1/memories/{memory_id}", status_code=204)
def memory_remove(memory_id: str, principal: Principal = Depends(current_principal)):
    if not delete_memory(principal, memory_id):
        raise HTTPException(status_code=404, detail="Memory not found or not owned by this Agent")


@app.get("/v1/negotiations")
def negotiations(limit: int = Query(default=50, ge=1, le=100),
                 principal: Principal = Depends(current_principal)):
    return {"items": list_threads(principal, limit)}


@app.post("/v1/negotiations", status_code=201)
def negotiation_start(body: ThreadStart, principal: Principal = Depends(current_principal)):
    if body.channel not in ("direct", "global"):
        raise HTTPException(status_code=422, detail="channel must be 'direct' or 'global'")
    if body.channel == "direct" and not body.recipients:
        raise HTTPException(status_code=422, detail="direct threads require at least one recipient")
    thread = start_thread(principal, body.subject, body.channel, body.recipients, body.related_memory_ids)
    post_message(principal, thread["id"], body.first_message)
    return next((t for t in list_threads(principal) if t["id"] == thread["id"]), thread)


@app.get("/v1/negotiations/{thread_id}")
def negotiation_read(thread_id: str, principal: Principal = Depends(current_principal)):
    thread = get_thread(principal, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Negotiation not found")
    return thread


@app.post("/v1/negotiations/{thread_id}/messages", status_code=201)
def negotiation_post(thread_id: str, body: MessageWrite,
                     principal: Principal = Depends(current_principal)):
    try:
        message = post_message(principal, thread_id, body.content, body.reply_to_message_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not message:
        raise HTTPException(status_code=404, detail="Negotiation not found or not visible to this Agent")
    return message


@app.patch("/v1/negotiations/{thread_id}")
def negotiation_resolve(thread_id: str, body: ResolveThread,
                        principal: Principal = Depends(current_principal)):
    thread = resolve_thread(principal, thread_id, body.status)
    if not thread:
        raise HTTPException(status_code=404, detail="Negotiation not found or only its creator may resolve it")
    return thread


# --------------------------------------------------------------------------- #
# Admin: credential management (protected by ADMIN_TOKENS)
# --------------------------------------------------------------------------- #
class CredentialIssue(BaseModel):
    tenant_id: str = "default"
    agent_id: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(default_factory=lambda: ["read", "write"])
    expires_at: str | None = None


def admin_principal(authorization: Annotated[str | None, Header()] = None) -> Principal:
    scheme, _, token = (authorization or "").partition(" ")
    principal = authenticate_admin(token if scheme.lower() == "bearer" else None)
    if not principal:
        raise HTTPException(status_code=401, detail="Provide a valid admin bearer token")
    return principal


@app.post("/v1/admin/credentials", status_code=201)
def admin_issue(body: CredentialIssue, _: Principal = Depends(admin_principal)):
    # The raw token is returned only here, once. Store it securely client-side.
    return issue_credential(body.tenant_id, body.agent_id, body.scopes, body.expires_at)


@app.get("/v1/admin/credentials")
def admin_list(tenant_id: str | None = None, include_revoked: bool = False,
               _: Principal = Depends(admin_principal)):
    return {"items": list_credentials(tenant_id, include_revoked)}


@app.post("/v1/admin/credentials/{cred_id}/revoke")
def admin_revoke(cred_id: str, _: Principal = Depends(admin_principal)):
    if not revoke_credential(cred_id):
        raise HTTPException(status_code=404, detail="Credential not found or already revoked")
    return {"revoked": True, "id": cred_id}


@app.post("/v1/admin/credentials/{cred_id}/rotate", status_code=201)
def admin_rotate(cred_id: str, _: Principal = Depends(admin_principal)):
    new = rotate_credential(cred_id)
    if not new:
        raise HTTPException(status_code=404, detail="Credential not found or already revoked")
    # New raw token returned only here, once.
    return new


@app.post("/v1/admin/reindex")
def admin_reindex(batch_size: int = Query(default=200, ge=1, le=1000),
                  _: Principal = Depends(admin_principal)):
    """Rebuild missing/failed chunk vectors after enabling or changing a provider."""
    return reindex_memory_chunks(batch_size)
