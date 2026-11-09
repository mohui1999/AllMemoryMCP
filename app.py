from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from memory_service import (
    Principal, ScopeError, authenticate, create_memory, delete_memory, get_memory,
    get_thread, initialize, list_threads, post_message, resolve_thread,
    search_memories, start_thread, update_memory,
)
from credentials import (
    authenticate_admin, issue_credential, list_credentials, revoke_credential,
    rotate_credential, verify_token,
)

app = FastAPI(title="AllMemoryMCP API", version="0.3.0",
              description="Global cross-agent memory and negotiation API (multi-tenant)")
WEB_DIR = Path(__file__).parent / "web"
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="viewer-assets")


@app.exception_handler(ScopeError)
async def scope_error_handler(request, exc: ScopeError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


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
             principal: Principal = Depends(current_principal)):
    # All authenticated agents in the same tenant can read all memories; ownership
    # is source metadata, not a visibility filter. Cross-tenant rows are excluded.
    return {"items": search_memories(q, limit, owner_agent_id, principal, offset)}


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
    item = update_memory(principal, memory_id, body.content, body.memory_type, body.tags)
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
