"""Match news headlines against the AcuTech client list.

Sources, in priority order:
  1. `clients.json` — names extracted from Egnyte project folders.
  2. `client_aliases.csv` — curated alias → canonical mappings (e.g.
     "Exxon" → "ExxonMobil", "ABG" → "Aditya Barla Group (ABG)"). Both
     directions are searched; matches always surface the canonical
     name to the dashboard.

A match is a word-boundary substring hit in the article title + body.
"""

import csv
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

CLIENTS_FILE = os.path.join(os.path.dirname(__file__), "clients.json")
ALIASES_FILE = os.path.join(os.path.dirname(__file__), "client_aliases.csv")

# Minimum search-term length (shorter terms over-match; "ACC", "AES" etc.
# are dropped from clients.json on this rule)
MIN_MATCH_LENGTH = 4

# Search terms that collide with common words / place names / first names —
# applied to both canonicals and aliases.
BLACKLISTED_TERMS = {
    "ashland", "portland", "firs", "dawn", "apex", "arc", "baker",
    "bay ltd", "delta", "eagle", "gem", "gulf", "hill", "iris",
    "jade", "mars", "nova", "pioneer", "premier", "summit", "titan",
    "trinity", "union", "vale", "vista", "york", "phoenix",
    "advocate", "champion", "compass", "frontier", "genesis",
    "global", "guardian", "horizon", "impact", "insight",
    "liberty", "matrix", "nexus", "oracle", "prime", "quest",
    "sterling", "venture",
}

# Module-level cache so main.py's per-article loop doesn't rebuild
# the table 300+ times per run.
_SEARCH_TABLE_CACHE: list[tuple[re.Pattern, str]] | None = None


def load_clients() -> list[str]:
    """Load client names from clients.json."""
    if not os.path.exists(CLIENTS_FILE):
        logger.warning("clients.json not found — run build_client_list.py first")
        return []
    with open(CLIENTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_aliases() -> list[tuple[str, str]]:
    """Load `client_aliases.csv`. Returns list of (canonical, alias) tuples.
    Both directions are searched; matches always return the canonical."""
    if not os.path.exists(ALIASES_FILE):
        return []
    out: list[tuple[str, str]] = []
    with open(ALIASES_FILE, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            canonical = (row.get("Canonical") or "").strip()
            alias = (row.get("Alias") or "").strip()
            if canonical and alias:
                out.append((canonical, alias))
    return out


def _build_search_table(
    clients: list[str], aliases: list[tuple[str, str]]
) -> list[tuple[re.Pattern, str]]:
    """Build a precompiled (regex, canonical_display) table.

    Longer search terms come first so 'ExxonMobil Corporation' resolves
    before 'Exxon' when both could fire. Canonicals from clients.json
    map to themselves; alias entries map to their canonical.
    """
    table: dict[str, str] = {}

    # Bulk clients.json entries — defensive guards apply (length + blacklist)
    for client in clients:
        key = client.lower()
        if len(key) < MIN_MATCH_LENGTH or key in BLACKLISTED_TERMS:
            continue
        table[key] = client

    # Curated aliases — trusted, no length/blacklist guards so short acronyms
    # ("ABG", "BP") work when the user has affirmatively listed them.
    for canonical, alias in aliases:
        canon_key = canonical.lower()
        if canon_key not in table:
            table[canon_key] = canonical
        table[alias.lower()] = canonical

    items = sorted(table.items(), key=lambda kv: len(kv[0]), reverse=True)
    return [(re.compile(r"\b" + re.escape(term) + r"\b"), canonical) for term, canonical in items]


def _get_search_table() -> list[tuple[re.Pattern, str]]:
    global _SEARCH_TABLE_CACHE
    if _SEARCH_TABLE_CACHE is None:
        clients = load_clients()
        aliases = load_aliases()
        _SEARCH_TABLE_CACHE = _build_search_table(clients, aliases)
        logger.info(
            "Client search table: %d terms (%d clients + %d aliases)",
            len(_SEARCH_TABLE_CACHE),
            len(clients),
            len(aliases),
        )
    return _SEARCH_TABLE_CACHE


def find_client_match(text: str, clients: list[str] | None = None) -> str | None:
    """Check if any client name or alias appears in the text.

    Returns the canonical display name if found, None otherwise. The
    `clients` arg is accepted for backwards compatibility with the
    previous signature but is ignored — the matcher always uses the
    cached merged table.
    """
    table = _get_search_table()
    if not table:
        return None

    text_lower = text.lower()
    for pattern, canonical in table:
        if pattern.search(text_lower):
            return canonical
    return None
