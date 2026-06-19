"""Read-API auth for PSM — a per-tool bearer-token gate, OFF by default.

PSM has no auth of its own; on the box it sits behind Caddy + Authentik forward-auth, which injects
`X-Authentik-*` headers. That stays the front door. This module adds an OPTIONAL second path so the
Acuity Agent (and any future service caller) can authenticate server-to-server over the `edge`
network with a `pull`-scoped bearer token.

Enforcement is controlled by the `PSM_REQUIRE_TOOL_TOKEN` env var:

- **off (default)** — `require_read` is a no-op. Behaviour is exactly as today: anything that can
  reach the service may read. This keeps the behind-Caddy deployment working unchanged.
- **on** (`1`/`true`/`yes`) — a request must carry EITHER an Authentik SSO session (a human in the
  portal, via the forward-auth headers) OR a valid `pull`-scoped bearer token. Otherwise 401.

Even with enforcement on, the service MUST remain behind forward-auth — the token is defence in
depth for the service-to-service call, not a replacement for the proxy.

Mirrors the Directory service's `app/deps.py` gate. Read-only: there is no write/admin gate here.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException, Request

import psm_tokens
from psm_access_log import record_read

# Token store lives on a persisted path. On the box the repo is mounted at /app (a bind mount, so the
# store survives container restarts); override with PSM_TOKENS_PATH to point at a dedicated volume.
TOKENS_PATH = Path(os.environ.get("PSM_TOKENS_PATH", "tokens.json"))


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in {"1", "true", "yes", "on"}


def require_tool_token() -> bool:
    """Whether bearer-token enforcement is on. Read live so tests can toggle the env var."""
    return _truthy(os.environ.get("PSM_REQUIRE_TOOL_TOKEN"))


def tokens_path() -> Path:
    """Token store path, read live so tests can repoint it via PSM_TOKENS_PATH."""
    return Path(os.environ.get("PSM_TOKENS_PATH", "tokens.json"))


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


def _has_sso(request: Request) -> bool:
    return bool(
        request.headers.get("x-authentik-username")
        or request.headers.get("x-authentik-uid")
    )


def _sso_principal(request: Request) -> str:
    """Best-effort `user:<id>` for an SSO caller — email if forwarded, else username, else `-`.
    Audit-only; PSM does not authorize on it (reads are uniform, decision 0022)."""
    ident = (
        request.headers.get("x-authentik-email")
        or request.headers.get("x-authentik-username")
        or request.headers.get("x-authentik-uid")
        or "-"
    )
    return f"user:{ident.strip()}"


def require_read(request: Request) -> None:
    """Gate read endpoints, and audit every authenticated read.

    When `PSM_REQUIRE_TOOL_TOKEN` is off (default) this is a no-op for *authorization* — the service
    is behind Caddy+Authentik on the box anyway — but the read is still recorded (the access audit
    is independent of enforcement). When on, require either an Authentik SSO session (a human in the
    portal) or a valid `pull`-scoped tool token.

    A read is logged with its principal (`tool:<subject>` for a token caller — never a human name;
    `user:<id>` for SSO / proxy-trusted) **only when it is allowed to proceed**. A rejected read is
    never logged here (the `HTTPException` is raised before `record_read`)."""
    record = psm_tokens.resolve(tokens_path(), _bearer(request))
    is_tool = psm_tokens.has_scope(record, "pull")

    if not require_tool_token():
        # Default off: the request proceeds (behind-Caddy behaviour unchanged). Still audit it —
        # a valid token caller is tagged `tool:`, everything else is a proxy-trusted human read.
        principal = f"tool:{record.get('subject')}" if is_tool else _sso_principal(request)
        record_read(request, principal)
        return

    if is_tool:
        record_read(request, f"tool:{record.get('subject')}")
        return
    if _has_sso(request):
        record_read(request, _sso_principal(request))
        return
    # Rejected: raise WITHOUT recording — an unauthorized read is not an audited read.
    raise HTTPException(
        status_code=401,
        detail="a valid pull token or SSO session is required",
    )