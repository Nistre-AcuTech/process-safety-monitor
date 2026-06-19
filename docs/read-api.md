# PSM read API — consumer integration guide

**Status:** v0 (2026-06-18). Read-only. The producer surface that makes the Process Safety Monitor
feed consumable by other AcuTech tools (the **Acuity Agent** first), server-to-server.
**Audience:** AcuTech apps that read the process-safety incident/news feed.
**Cross-project standard (the rules every consumer follows):**
[wiki/software/notes/smartpids-integration-standard.md](../../wiki/software/notes/smartpids-integration-standard.md).

PSM scans global news for process-safety events, cross-references them against the AcuTech client
list, and stores the result in Postgres. This doc is the stable surface to build against; anything
not listed here is internal and may change.

> ⚠️ **The service stays behind Caddy forward-auth.** PSM has no auth of its own; on the box it sits
> behind the portal's Authentik forward-auth, which is the front door. The per-tool bearer token
> below is *defence in depth* for the agent's call over the internal `edge` network — **not** a
> replacement for the proxy. Do not expose PSM to the public internet on the strength of the token.

---

## TL;DR for a new consumer

1. **Vendor the client.** Copy [`clients/python/psm_client.py`](../clients/python/psm_client.py)
   into your app and pin it (don't edit the vendored copy — sync it from PSM). It's stdlib-only.
2. **Get a per-tool token.** Ask the PSM owner to mint one (`pull` scope). See *Go-live* below.
   Store it as your app's `PSM_TOOL_TOKEN` (a secret in env, never in code/repo).
3. **Point at the service.** Set `PSM_URL` (e.g. `http://psm-web:8000` on the box's `edge` network).
4. **Call it:**
   ```python
   from psm_client import PSMClient
   c = PSMClient(base_url=PSM_URL, token=TOOL_TOKEN)
   feed = c.events()          # GET /api/events -> {"last_updated", "events": [...]}
   items = c.event_list()     # just the events list
   ```

---

## Endpoints (read-only)

All `/api/*` endpoints are gated by `require_read` (see *Auth*). `/healthz` is never gated.

| Method | Path | Returns |
|---|---|---|
| GET | `/healthz` | `{"ok": true}` — liveness, ungated |
| GET | `/api/events` | `{"last_updated": ISO8601\|null, "events": [Event, …]}` — full feed, newest first |
| GET | `/api/events/{id}` | a single `Event` (with `id`); `404` if unknown |
| GET | `/api/summary` | `{"last_updated", "total_events", "events_with_client", "incident_clusters"}` |

There are **no write endpoints.** The feed is produced by the scanner job (`main.py`), not the API.

### `Event` shape

Each event carries (fields with no value are omitted):

| Field | Type | Notes |
|---|---|---|
| `title` | string | original headline |
| `title_en` | string | English translation when the source was non-English |
| `url` | string | canonical article URL (the natural key) |
| `source` | string | outlet name |
| `date` | string | ISO 8601 publish time |
| `country` | string | source country (may be empty) |
| `keywords` | string[] | which process-safety keywords matched |
| `client` | string\|null | matched AcuTech client name, or null |
| `description` | string | article summary |
| `cluster_id` | int | groups articles about the same incident |
| `id` | int | numeric id (only on `/api/events/{id}`) |

---

## Auth

PSM has two ways in. As a server-to-server consumer you care about the **bearer token**.

- **Caddy forward-auth (the front door, always on).** On the box, the portal's Authentik
  forward-auth injects `X-Authentik-*` headers. PSM trusts the proxy. This is unchanged.
- **Per-tool bearer token (`Authorization: Bearer <token>`).** Only the **sha256** of each token is
  stored (`tokens.json`); the raw token is shown once at mint. Scope is `pull` (read). Mint on the
  box, store as the consumer's `PSM_TOOL_TOKEN` env secret, rotate by re-minting.

**Enforcement is OFF by default**, controlled by `PSM_REQUIRE_TOOL_TOKEN`:

- **off (default)** — reads are open to anything that can reach the service (i.e. behind Caddy).
  Behaviour is exactly as before this change. Wire the token in **now** and the flip is a no-op.
- **on** (`1`/`true`/`yes`) — a request must carry **either** an Authentik SSO session (a human in
  the portal) **or** a valid `pull` token; otherwise `401`.

Mirrors the Directory service's gate (`directory/app/deps.py`). Read-only — no write/admin path.

### Confused-deputy note

PSM reads are **uniform** — there is no per-user scoping; every authenticated AcuTech user (and the
agent) sees the same feed. Per [decision 0022](../../wiki/decisions/0022-acuity-agent-inherits-suite-user-access.md),
that makes PSM like Directory: the agent reading the feed on a user's behalf grants **nothing
extra**, so a flat `pull` token over the `edge` network is safe — PSM never *authorizes* on a
forwarded user identity.

### Access audit

Every **authenticated** read is recorded as one structured log line (no DB table, no migration —
the read path stays side-effect-free against Postgres), matching the sibling suite apps
(LoadLevel/Directory). The line carries:

- **principal** — `tool:<subject>` for a token caller (e.g. `tool:acuity-agent`), `user:<id>` for a
  human via SSO / behind-Caddy. A token caller is **never** logged as a human name.
- **on_behalf_of** — any end-user identity a *tool* forwarded (`X-On-Behalf-Of-User`, or the
  Authentik headers it presented), recorded **audit-only** — PSM does not authorize on it (reads are
  uniform). `-` when absent and for human callers.
- **resource** — the request method + path read.

Token reads log at **INFO**; human reads at **DEBUG**, so the default level is the "what are the
tools pulling" view without portal/UI noise. The logger is `psm.access`. Auditing is best-effort:
it never raises and **a rejected (401) read is never logged** — only reads that proceed are audited.

---

## Go-live (minting the agent's token)

The web service runs as container **`psm-web`**. To enforce the gate and mint the agent a token:

```bash
# 1) Mint the Acuity Agent a pull token (prints the raw token ONCE):
docker exec psm-web python -m mint_token acuity-agent --scopes pull

# 2) Turn enforcement on (so the token is actually checked). In ops/docker-compose.yml,
#    set on the `web` service environment:  PSM_REQUIRE_TOOL_TOKEN: "1"
#    then:  docker compose -f ops/docker-compose.yml up -d web

# 3) Put the printed token in the agent's env as PSM_TOOL_TOKEN, point PSM_URL at
#    http://psm-web:8000, and wire the connector.
```

Rotate by re-running step 1 (minting revokes the subject's previous token). Revoke with
`docker exec psm-web python -m mint_token acuity-agent --revoke`.

The token store defaults to `tokens.json` in the working dir (`/app`, a bind mount, so it survives
restarts). Point `PSM_TOKENS_PATH` at a dedicated volume if you prefer. **`tokens.json` is
gitignored-by-effect** (it lives under the mounted repo but only holds hashes); never commit it.

---

## Vendoring the client

Stdlib-only (`urllib`), one transport path, thin typed methods — same shape as the SmartPIDs client.
Copy `clients/python/psm_client.py` into the consumer and pin it; **don't edit the vendored copy** —
sync it from PSM when it changes. If you need a field/endpoint PSM doesn't expose, ask the PSM owner
to add it (one typed method, shared by every consumer) rather than scraping or forking.
