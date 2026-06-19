"""Dashboard web app for process-safety-monitor.

Serves the existing static dashboard (docs/) plus a JSON API backed by Postgres.
Runs on the acutech-tools box behind the portal (forward-auth). Replaces the
GitHub Pages + events.json delivery path.

    uvicorn app:app --host 0.0.0.0 --port 8000

Read API for service callers (the Acuity Agent): GET /api/events, /api/events/{id},
/api/summary. These are gated by `psm_auth.require_read` — a no-op by default (the service sits
behind Caddy forward-auth) and a per-tool bearer gate when PSM_REQUIRE_TOOL_TOKEN is set. The
service MUST remain behind forward-auth either way. See docs/read-api.md.
"""

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from psm_auth import require_read
from psm_store import get_event, get_payload, get_summary, init_db

app = FastAPI(title="Process Safety Monitor")


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/api/events", dependencies=[Depends(require_read)])
def api_events() -> JSONResponse:
    """The full incident/news feed: {last_updated, events}."""
    return JSONResponse(get_payload())


@app.get("/api/events/{event_id}", dependencies=[Depends(require_read)])
def api_event(event_id: int) -> JSONResponse:
    """A single event by numeric id. 404 if unknown."""
    ev = get_event(event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="event not found")
    return JSONResponse(ev)


@app.get("/api/summary", dependencies=[Depends(require_read)])
def api_summary() -> JSONResponse:
    """Lightweight counts for the feed (no event bodies)."""
    return JSONResponse(get_summary())


# Static dashboard last so /api/* + /healthz win. html=True serves docs/index.html at /.
app.mount("/", StaticFiles(directory="docs", html=True), name="dashboard")
