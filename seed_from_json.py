"""One-time migration: load the legacy docs/data/events.json into Postgres.

    python seed_from_json.py [path/to/events.json]
"""

import json
import sys

from psm_store import init_db, save_events

path = sys.argv[1] if len(sys.argv) > 1 else "docs/data/events.json"

with open(path, encoding="utf-8") as f:
    data = json.load(f)

events = data["events"] if isinstance(data, dict) else data
init_db()
save_events(events)
print(f"seeded {len(events)} events from {path}")
