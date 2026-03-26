"""Match news headlines against the client list extracted from Egnyte project folders."""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

CLIENTS_FILE = os.path.join(os.path.dirname(__file__), "clients.json")

# Minimum name length to match (avoids false positives from short acronyms)
MIN_MATCH_LENGTH = 4

# Names that are also common English words or place names — skip these
FALSE_POSITIVE_NAMES = {
    "ashland", "portland", "firs", "dawn", "apex", "arc", "baker",
    "bay ltd", "delta", "eagle", "gem", "gulf", "hill", "iris",
    "jade", "mars", "nova", "pioneer", "premier", "summit", "titan",
    "trinity", "union", "vale", "vista", "york", "phoenix",
    "advocate", "champion", "compass", "frontier", "genesis",
    "global", "guardian", "horizon", "impact", "insight",
    "liberty", "matrix", "nexus", "oracle", "prime", "quest",
    "sterling", "venture",
}


def load_clients() -> list[str]:
    """Load client names from clients.json."""
    if not os.path.exists(CLIENTS_FILE):
        logger.warning("clients.json not found — run build_client_list.py first")
        return []

    with open(CLIENTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def find_client_match(text: str, clients: list[str] | None = None) -> str | None:
    """Check if any client name appears in the given text.

    Returns the client name if found, None otherwise.
    Uses word-boundary matching to avoid partial matches.
    """
    if clients is None:
        clients = load_clients()

    if not clients:
        return None

    text_lower = text.lower()

    # Check longest names first (more specific matches take priority)
    for client in sorted(clients, key=len, reverse=True):
        if len(client) < MIN_MATCH_LENGTH:
            continue

        client_lower = client.lower()

        if client_lower in FALSE_POSITIVE_NAMES:
            continue

        # Use word boundary matching to avoid partial matches
        # e.g., "Shell" should match "Shell refinery" but not "shelling"
        pattern = r'\b' + re.escape(client_lower) + r'\b'
        if re.search(pattern, text_lower):
            return client

    return None
