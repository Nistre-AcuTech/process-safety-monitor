"""Hermetic tests for the PSM read-API token path.

No network, no Postgres, no API key. We test:
  - the sha256 token store (mint / resolve / rotate / revoke),
  - the `require_read` gate's three behaviours (default-off no-op; on accepts valid bearer / SSO;
    on rejects missing or wrong token),
  - the FastAPI wiring of the gate on /api/events without touching Postgres (the store call is
    monkeypatched, the auth dependency is the thing under test).

Run: pytest -q
"""
import importlib
import logging
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

# Make repo root importable when pytest is invoked from elsewhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psm_access_log  # noqa: E402
import psm_auth  # noqa: E402
import psm_tokens  # noqa: E402


# --- a minimal fake Request: require_read reads headers; record_read reads method+url.path ----
class _FakeURL:
    def __init__(self, path: str):
        self.path = path


class FakeRequest:
    def __init__(self, headers: dict | None = None, method: str = "GET", path: str = "/api/events"):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.method = method
        self.url = _FakeURL(path)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the token store at a temp file and reset enforcement to off."""
    path = tmp_path / "tokens.json"
    monkeypatch.setenv("PSM_TOKENS_PATH", str(path))
    monkeypatch.delenv("PSM_REQUIRE_TOOL_TOKEN", raising=False)
    return path


# --- token store -------------------------------------------------------------

def test_mint_resolve_and_scope(store):
    tok = psm_tokens.mint_tool(store, "acuity-agent", ["pull"])
    rec = psm_tokens.resolve(store, tok)
    assert rec and rec["subject"] == "acuity-agent"
    assert psm_tokens.has_scope(rec, "pull")
    assert not psm_tokens.has_scope(rec, "write")


def test_only_hash_is_stored(store):
    tok = psm_tokens.mint_tool(store, "acuity-agent")
    raw = store.read_text(encoding="utf-8")
    assert tok not in raw  # raw token never persisted, only its sha256


def test_mint_rotates_previous(store):
    old = psm_tokens.mint_tool(store, "acuity-agent")
    new = psm_tokens.mint_tool(store, "acuity-agent")
    assert psm_tokens.resolve(store, old) is None  # rotated out
    assert psm_tokens.resolve(store, new) is not None


def test_revoke(store):
    tok = psm_tokens.mint_tool(store, "acuity-agent")
    assert psm_tokens.revoke_tool(store, "acuity-agent") == 1
    assert psm_tokens.resolve(store, tok) is None


def test_resolve_unknown_or_empty(store):
    psm_tokens.mint_tool(store, "acuity-agent")
    assert psm_tokens.resolve(store, "garbage") is None
    assert psm_tokens.resolve(store, None) is None


# --- require_read gate -------------------------------------------------------

def test_gate_off_by_default_allows_anonymous(store):
    # Default off: anyone reachable may read (behind-Caddy behaviour unchanged).
    assert psm_auth.require_read(FakeRequest()) is None


def test_gate_on_rejects_missing_token(store, monkeypatch):
    monkeypatch.setenv("PSM_REQUIRE_TOOL_TOKEN", "1")
    with pytest.raises(HTTPException) as ei:
        psm_auth.require_read(FakeRequest())
    assert ei.value.status_code == 401


def test_gate_on_rejects_bad_token(store, monkeypatch):
    psm_tokens.mint_tool(store, "acuity-agent")
    monkeypatch.setenv("PSM_REQUIRE_TOOL_TOKEN", "1")
    req = FakeRequest({"Authorization": "Bearer not-a-real-token"})
    with pytest.raises(HTTPException) as ei:
        psm_auth.require_read(req)
    assert ei.value.status_code == 401


def test_gate_on_accepts_valid_bearer(store, monkeypatch):
    tok = psm_tokens.mint_tool(store, "acuity-agent", ["pull"])
    monkeypatch.setenv("PSM_REQUIRE_TOOL_TOKEN", "1")
    req = FakeRequest({"Authorization": f"Bearer {tok}"})
    assert psm_auth.require_read(req) is None  # no exception => authenticated


def test_gate_on_accepts_sso_session(store, monkeypatch):
    # A human in the portal: forward-auth headers present, no token needed.
    monkeypatch.setenv("PSM_REQUIRE_TOOL_TOKEN", "1")
    req = FakeRequest({"X-Authentik-Username": "alice"})
    assert psm_auth.require_read(req) is None


def test_gate_on_rejects_non_pull_scope(store, monkeypatch):
    # A token without `pull` (hypothetical) must not pass the read gate.
    tok = psm_tokens.mint_tool(store, "weird", ["something-else"])
    monkeypatch.setenv("PSM_REQUIRE_TOOL_TOKEN", "1")
    req = FakeRequest({"Authorization": f"Bearer {tok}"})
    with pytest.raises(HTTPException):
        psm_auth.require_read(req)


# --- access audit logging ----------------------------------------------------
# One structured line per AUTHENTICATED read: principal (tool:<subject> / user:<id>, never a human
# name), any forwarded on-behalf-of identity (audit-only), and the resource. A rejected read is NOT
# logged. Best-effort — never raises. Mirrors LoadLevel's access_log.


def test_record_read_logs_tool_principal_obo_and_resource(caplog):
    req = FakeRequest(
        {"X-On-Behalf-Of-User": "carol@acutech.com"},
        method="GET",
        path="/api/events",
    )
    with caplog.at_level(logging.INFO, logger="psm.access"):
        psm_access_log.record_read(req, "tool:acuity-agent")
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "principal=tool:acuity-agent" in msg
    assert "on_behalf_of=carol@acutech.com" in msg
    assert "resource=GET" in msg and "/api/events" in msg


def test_record_read_human_logs_at_debug_without_obo(caplog):
    # A human (user:) is logged at DEBUG (not INFO) and never carries an on-behalf-of.
    req = FakeRequest({"X-On-Behalf-Of-User": "ignored@x.com"})
    with caplog.at_level(logging.INFO, logger="psm.access"):
        psm_access_log.record_read(req, "user:alice@acutech.com")
    assert caplog.records == []  # nothing at INFO
    with caplog.at_level(logging.DEBUG, logger="psm.access"):
        psm_access_log.record_read(req, "user:alice@acutech.com")
    msg = caplog.records[-1].getMessage()
    assert "principal=user:alice@acutech.com" in msg
    assert "on_behalf_of=-" in msg  # humans never get an on-behalf-of recorded


def test_record_read_never_raises():
    # Best-effort: a malformed request object must not break the read.
    assert psm_access_log.record_read(object(), "tool:acuity-agent") is None


def test_gate_logs_token_read_with_principal_obo_and_resource(store, monkeypatch, caplog):
    tok = psm_tokens.mint_tool(store, "acuity-agent", ["pull"])
    monkeypatch.setenv("PSM_REQUIRE_TOOL_TOKEN", "1")
    req = FakeRequest(
        {"Authorization": f"Bearer {tok}", "X-On-Behalf-Of-User": "dave@acutech.com"},
        path="/api/summary",
    )
    with caplog.at_level(logging.INFO, logger="psm.access"):
        assert psm_auth.require_read(req) is None  # authenticated
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "principal=tool:acuity-agent" in msg
    assert "on_behalf_of=dave@acutech.com" in msg
    assert "/api/summary" in msg


def test_gate_rejected_read_is_not_logged(store, monkeypatch, caplog):
    # Enforcement on, no token/SSO → 401 AND no audit line (an unauthorized read is not a read).
    monkeypatch.setenv("PSM_REQUIRE_TOOL_TOKEN", "1")
    with caplog.at_level(logging.DEBUG, logger="psm.access"):
        with pytest.raises(HTTPException):
            psm_auth.require_read(FakeRequest())
    assert caplog.records == []


def test_gate_off_logs_proxy_trusted_read_as_user(store, caplog):
    # Default-off: a behind-Caddy read still proceeds; it is audited as a user (proxy-trusted),
    # never as a tool.
    with caplog.at_level(logging.DEBUG, logger="psm.access"):
        assert psm_auth.require_read(FakeRequest()) is None
    msg = caplog.records[-1].getMessage()
    assert "principal=user:" in msg
    assert "principal=tool:" not in msg


# --- FastAPI wiring (no Postgres) -------------------------------------------

def test_app_events_gated_without_db(store, monkeypatch):
    """The /api/events route enforces the gate before touching the store. We stub init_db +
    get_payload so no Postgres is needed, then assert: gate-on rejects anon, accepts a valid token."""
    from fastapi.testclient import TestClient

    # Stub the DB-touching pieces before importing app.
    import psm_store
    monkeypatch.setattr(psm_store, "init_db", lambda: None)
    monkeypatch.setattr(psm_store, "get_payload", lambda: {"last_updated": None, "events": []})

    app_mod = importlib.import_module("app")
    importlib.reload(app_mod)  # re-bind get_payload/init_db imports to the stubs
    client = TestClient(app_mod.app)

    monkeypatch.setenv("PSM_REQUIRE_TOOL_TOKEN", "1")
    assert client.get("/api/events").status_code == 401

    tok = psm_tokens.mint_tool(store, "acuity-agent", ["pull"])
    r = client.get("/api/events", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json() == {"last_updated": None, "events": []}

    # healthz is never gated.
    assert client.get("/healthz").json() == {"ok": True}
