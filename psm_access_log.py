"""Lightweight access/activity audit for the PSM token-reachable read API.

PSM keeps no audit table — the feed is produced by the scanner, not by users, and reads were
previously unrecorded: nothing said *who pulled the feed, when*. This module adds that, mirroring
the sibling suite apps (LoadLevel's `app/auth/access_log.py`): a structured **log line**, not a DB
table, so it needs no migration and keeps the read path side-effect-free against Postgres.

One INFO line per token-authenticated read carries:

  - **principal** — `tool:<subject>` for a token caller (the Acuity Agent over the `edge` network),
    `user:<email>` for a human in the portal via SSO. A token caller is *never* recorded as a human
    actor; the prefix keeps the two namespaces distinct. NEVER a human name.
  - **on_behalf_of** — any forwarded end-user identity a tool presented (`X-On-Behalf-Of-User`, or
    the Authentik headers it forwarded). PSM does **not** authorize on it (reads are uniform, per
    decision 0022 — the "Directory case"), but recording who the tool acted *for* is the point of
    the audit. `-` when absent.
  - **resource** — the request method + path that was read.
  - timestamp — supplied by the logging framework.

Humans hitting the read API through SSO are logged too, at DEBUG, so the default INFO level is the
"what are the tools doing" view without drowning in normal portal/UI traffic. Best-effort: never
raises, never breaks a read — auditing must not be able to take the feed down.
"""
from __future__ import annotations

import logging

from fastapi import Request

log = logging.getLogger("psm.access")

# Identity a trusted tool may forward about the end user it is acting for. PSM does not scope reads
# by it (uniform data — decision 0022 "Directory case"), but it is recorded for audit.
_ON_BEHALF_HEADERS = (
    "x-on-behalf-of-user",
    "x-authentik-username",
    "x-authentik-email",
)


def _on_behalf_of(request: Request) -> str:
    for h in _ON_BEHALF_HEADERS:
        v = request.headers.get(h)
        if v:
            return v.strip()
    return "-"


def record_read(request: Request, principal: str) -> None:
    """Emit one audit line for a read that passed `require_read`.

    Token principals (``tool:…``) log at INFO; humans (``user:…``) at DEBUG so the default level
    surfaces server-to-server activity without UI noise. Never raises — auditing must not break a read.
    """
    try:
        is_tool = principal.startswith("tool:")
        log.log(
            logging.INFO if is_tool else logging.DEBUG,
            "read principal=%s on_behalf_of=%s resource=%s %s",
            principal,
            _on_behalf_of(request) if is_tool else "-",
            request.method,
            request.url.path,
        )
    except Exception:  # noqa: BLE001 — audit is best-effort, never fail the request
        log.warning("access_log: failed to record read for principal=%r", principal)
