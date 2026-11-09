"""Managed agent credential service.

Issues, verifies, revokes, and rotates agent tokens at runtime, replacing the
need to hard-code every agent in AGENT_TOKENS. The raw token is returned only
once at issue time; only its SHA-256 hash is persisted. Tokens carry scopes
("read"/"write") and an optional expiry; rotation revokes the old token and
issues a new one for the same agent/scopes.

Admin operations that call this module are protected by the static ADMIN_TOKENS
env var (kept separate from agent credentials). See app.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from db import AgentCredential, Principal, now, session


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_expiry(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return str(value)


def issue_credential(tenant_id: str, agent_id: str, scopes: list[str] | None = None,
                    expires_at: Any = None) -> dict[str, Any]:
    scopes = scopes or ["read", "write"]
    token = secrets.token_urlsafe(32)
    with session() as s:
        c = AgentCredential(
            id=str(uuid4()), tenant_id=tenant_id, agent_id=agent_id,
            token_hash=hash_token(token), scopes=json.dumps(scopes),
            expires_at=_parse_expiry(expires_at), revoked_at=None, created_at=now(),
        )
        s.add(c)
        s.flush()
        return {
            "id": c.id, "tenant_id": tenant_id, "agent_id": agent_id,
            "token": token, "scopes": scopes, "expires_at": c.expires_at,
            "created_at": c.created_at,
        }


def verify_token(token: str) -> Principal | None:
    h = hash_token(token)
    with session() as s:
        c = s.execute(
            select(AgentCredential).where(AgentCredential.token_hash == h)
        ).scalars().first()
        if not c or c.revoked_at:
            return None
        if c.expires_at and c.expires_at < now():
            return None
        return Principal(
            tenant_id=c.tenant_id, agent_id=c.agent_id,
            scopes=tuple(json.loads(c.scopes)),
        )


def revoke_credential(cred_id: str) -> bool:
    with session() as s:
        c = s.get(AgentCredential, cred_id)
        if not c or c.revoked_at:
            return False
        c.revoked_at = now()
        return True


def delete_credential(cred_id: str) -> bool:
    """Remove a credential row entirely. Memories and audit records are kept.

    Revoke is reversible only in the sense that the row is kept for audit; this
    is the explicit "remove this agent's access record" operation used by the
    admin console. It never deletes memories uploaded by that agent.
    """
    with session() as s:
        c = s.get(AgentCredential, cred_id)
        if not c:
            return False
        s.delete(c)
        return True


def rotate_credential(cred_id: str) -> dict[str, Any] | None:
    with session() as s:
        c = s.get(AgentCredential, cred_id)
        if not c or c.revoked_at:
            return None
        c.revoked_at = now()
        token = secrets.token_urlsafe(32)
        new = AgentCredential(
            id=str(uuid4()), tenant_id=c.tenant_id, agent_id=c.agent_id,
            token_hash=hash_token(token), scopes=c.scopes,
            expires_at=c.expires_at, revoked_at=None, created_at=now(),
        )
        s.add(new)
        s.flush()
        return {
            "id": new.id, "tenant_id": new.tenant_id, "agent_id": new.agent_id,
            "token": token, "scopes": json.loads(new.scopes),
            "expires_at": new.expires_at, "created_at": new.created_at,
        }


def list_credentials(tenant_id: str | None = None, include_revoked: bool = False) -> list[dict[str, Any]]:
    with session() as s:
        stmt = select(AgentCredential)
        if tenant_id:
            stmt = stmt.where(AgentCredential.tenant_id == tenant_id)
        if not include_revoked:
            stmt = stmt.where(AgentCredential.revoked_at.is_(None))
        rows = s.execute(stmt.order_by(AgentCredential.created_at.desc())).scalars().all()
        return [
            {
                "id": r.id, "tenant_id": r.tenant_id, "agent_id": r.agent_id,
                "scopes": json.loads(r.scopes), "expires_at": r.expires_at,
                "revoked_at": r.revoked_at, "created_at": r.created_at,
            }
            for r in rows
        ]


# --------------------------------------------------------------------------- #
# Admin authentication (static ADMIN_TOKENS; separate from agent credentials)
# --------------------------------------------------------------------------- #
def configured_admin_tokens() -> dict[str, str]:
    raw = os.getenv("ADMIN_TOKENS", "{}")
    try:
        tokens = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ADMIN_TOKENS must be a JSON object") from exc
    if not isinstance(tokens, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in tokens.items()
    ):
        raise RuntimeError("ADMIN_TOKENS must map admin name -> token")
    return tokens


def authenticate_admin(token: str | None) -> Principal | None:
    if not token:
        return None
    for name, expected in configured_admin_tokens().items():
        if secrets.compare_digest(token, expected):
            # tenant "*" signals cross-tenant admin authority.
            return Principal(tenant_id="*", agent_id=name, scopes=("admin",))
    return None
