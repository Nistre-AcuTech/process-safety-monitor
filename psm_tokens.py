"""Per-tool bearer tokens for the PSM read API.

A service caller (the Acuity Agent, primarily) authenticates server-to-server with a **pull**-scoped
token so it can read the incident/news feed. Only the sha256 of each token is stored in the token
store; the raw token is shown once at mint time. Minting for a subject revokes that subject's
previous tokens (regenerate == rotate).

This mirrors the Directory service's `app/tokens.py` (sha256 per-tool tokens). PSM is **read-only**:
the only scope is `pull`. There is no write path and no admin-via-token path.

IMPORTANT — this is a *defence-in-depth* path for the agent's service-to-service call on the `edge`
network. The PSM web service still has NO auth of its own by default and MUST remain behind Caddy
forward-auth on the box. The bearer token is only enforced when PSM_REQUIRE_TOOL_TOKEN is set.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load(store: Path) -> dict[str, dict]:
    if not store.is_file():
        return {}
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(store: Path, data: dict[str, dict]) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, store)  # atomic on the same filesystem


def mint_tool(store: Path, tool: str, scopes: list[str] | None = None) -> str:
    """Create a fresh token for `tool` with `scopes` (default ["pull"]), revoking its previous ones.
    Returns the raw token (shown once — not recoverable)."""
    token = secrets.token_urlsafe(32)
    record = {
        "type": "tool",
        "subject": tool,
        "scopes": sorted(set(scopes or ["pull"])),
        "created": datetime.now(timezone.utc).isoformat(),
    }
    with _LOCK:
        data = {h: v for h, v in _load(store).items() if v.get("subject") != tool}
        data[_hash(token)] = record
        _save(store, data)
    return token


def revoke_tool(store: Path, tool: str) -> int:
    """Delete all tokens for `tool`. Returns how many were removed."""
    with _LOCK:
        data = _load(store)
        kept = {h: v for h, v in data.items() if v.get("subject") != tool}
        removed = len(data) - len(kept)
        if removed:
            _save(store, kept)
        return removed


def resolve(store: Path, token: str | None) -> dict | None:
    """Resolve a raw bearer token to its record, or None."""
    if not token:
        return None
    return _load(store).get(_hash(token))


def has_scope(record: dict | None, scope: str) -> bool:
    return bool(record) and scope in record.get("scopes", [])