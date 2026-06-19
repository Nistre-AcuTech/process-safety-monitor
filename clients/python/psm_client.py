"""Canonical PSM (Process Safety Monitor) read client — the ONE way AcuTech tools read the
process-safety incident/news feed.

PSM owns the feed (a Postgres-backed list of process-safety news/incident events, cross-referenced
against the client list), so it ships this client. Consumer apps (the Acuity Agent first) **vendor a
copy** of this file and pin it — DO NOT edit the vendored copy; sync it from here
(`process-safety-monitor/clients/python/psm_client.py`).

Stdlib-only (urllib) so it drops into any app with no new dependency. Carries a per-tool bearer
token; PSM checks it (by token scope `pull`) only when PSM_REQUIRE_TOOL_TOKEN is on server-side. The
PSM service is **read-only** — there is no write path.

Usage:
    c = PSMClient(base_url="http://psm-web:8000", token=TOOL_TOKEN)
    c.health()                  -> {"reachable": True, "ok": True}
    feed = c.events()           -> {"last_updated": ..., "events": [ {...}, ... ]}
    items = c.event_list()      -> the events list directly
    one = c.event(42)           -> a single event by id (None if 404)
    summary = c.summary()       -> {"total_events", "events_with_client", ...}

Mirrors the SmartPIDs client's structure (one transport path, thin typed methods, get_json escape
hatch, never-raising health()).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class PSMError(RuntimeError):
    """PSM unreachable or returned an error (live calls only)."""


class PSMClient:
    def __init__(
        self,
        base_url: str = "",
        token: str | None = None,
        timeout: float = 10.0,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.timeout = timeout

    # --- low-level GET (the ONE transport path) ----------------------------
    # Every call funnels through here: base-url join, bearer auth, timeout, and
    # JSON decode live in exactly one place (DRY). Add new endpoints as thin
    # typed methods that call this — never re-implement the transport.
    def _get(self, path: str, params: dict | None = None) -> dict:
        qs = ""
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean:
                qs = "?" + urllib.parse.urlencode(clean)
        req = urllib.request.Request(f"{self.base_url}{path}{qs}")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_json(self, path: str, **params) -> dict:
        """Supported escape hatch for a GET endpoint this client doesn't wrap yet.

        Reuses the single transport path above (auth header, timeout, JSON decode) so a consumer
        NEVER hand-rolls urllib. STOPGAP: when you reach for it, ask PSM to add a typed method so
        every consumer shares it. Raises urllib errors on failure."""
        return self._get(path, params or None)

    # --- pull --------------------------------------------------------------
    def health(self) -> dict:
        """Liveness probe — never raises."""
        try:
            return {"reachable": True, **self._get("/healthz")}
        except Exception as e:  # noqa: BLE001 — health must not throw
            return {"reachable": False, "error": str(e)}

    def events(self) -> dict:
        """GET /api/events — the full feed `{last_updated, events}`. Raises PSMError on failure."""
        return self._fetch("/api/events")

    def event_list(self) -> list[dict]:
        """Just the events list from /api/events (newest first), or [] if absent."""
        data = self.events()
        items = data.get("events")
        return items if isinstance(items, list) else []

    def event(self, event_id: int) -> dict | None:
        """GET /api/events/{id} — a single event, or None if PSM returns 404."""
        try:
            return self._get(f"/api/events/{int(event_id)}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise PSMError(f"PSM error {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise PSMError(f"PSM unreachable: {e}") from e

    def summary(self) -> dict:
        """GET /api/summary — lightweight feed counts. Raises PSMError on failure."""
        return self._fetch("/api/summary")

    # --- shared error wrapping for the always-present endpoints -------------
    def _fetch(self, path: str) -> dict:
        try:
            return self._get(path)
        except urllib.error.HTTPError as e:
            raise PSMError(f"PSM error {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise PSMError(f"PSM unreachable: {e}") from e
