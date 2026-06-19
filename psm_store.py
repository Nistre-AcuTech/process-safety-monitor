"""Postgres persistence for process-safety-monitor.

Replaces the old events.json-in-git approach. Exposes the same two functions
main.py already calls — load_existing_events() / save_events(events) — so the
scan/merge/cluster logic is unchanged; only where the data lives changed.
"""

import os
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, JSON, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://psm:psm@localhost:5432/psm"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, future=True)
Base = declarative_base()

# Fields carried on each event dict (matches the old JSON shape exactly).
EVENT_FIELDS = [
    "title", "title_en", "url", "source", "date",
    "country", "keywords", "client", "description", "cluster_id",
]


class Event(Base):
    __tablename__ = "event"
    id = Column(Integer, primary_key=True)
    url = Column(Text, unique=True, nullable=False, index=True)
    title = Column(Text)
    title_en = Column(Text)
    source = Column(Text)
    date = Column(Text, index=True)          # ISO string — parity with old JSON
    country = Column(Text)
    keywords = Column(JSON)                   # list[str]
    client = Column(Text, index=True)
    description = Column(Text)
    cluster_id = Column(Integer, index=True)

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in EVENT_FIELDS}
        return {k: v for k, v in d.items() if v is not None}


class Meta(Base):
    __tablename__ = "meta"
    key = Column(String, primary_key=True)
    value = Column(Text)


_initialized = False


def init_db() -> None:
    global _initialized
    if not _initialized:
        Base.metadata.create_all(engine)
        _initialized = True


def _all_events(session) -> list[Event]:
    return session.query(Event).order_by(Event.date.desc().nulls_last()).all()


def load_existing_events() -> list[dict]:
    """All stored events as dicts, newest first — drop-in for the old JSON loader."""
    init_db()
    with SessionLocal() as s:
        return [e.to_dict() for e in _all_events(s)]


def save_events(events: list[dict]) -> None:
    """Replace the stored set with `events` (already merged + capped by main.py)."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as s:
        s.query(Event).delete()
        for e in events:
            s.add(Event(**{f: e.get(f) for f in EVENT_FIELDS}))
        meta = s.get(Meta, "last_updated") or Meta(key="last_updated")
        meta.value = now
        s.merge(meta)
        s.commit()


def get_payload() -> dict:
    """Dashboard API shape: {last_updated, events} — same as the old events.json."""
    init_db()
    with SessionLocal() as s:
        meta = s.get(Meta, "last_updated")
        return {
            "last_updated": meta.value if meta else None,
            "events": [e.to_dict() for e in _all_events(s)],
        }


def get_event(event_id: int) -> dict | None:
    """A single stored event by its numeric id, or None. Read-only."""
    init_db()
    with SessionLocal() as s:
        ev = s.get(Event, event_id)
        if ev is None:
            return None
        d = ev.to_dict()
        d["id"] = ev.id
        return d


def get_summary() -> dict:
    """Lightweight counts for a service caller that wants the shape of the feed without pulling
    every event: total events, how many are matched to a client, distinct incident clusters, and
    last_updated. Read-only."""
    init_db()
    with SessionLocal() as s:
        events = _all_events(s)
        meta = s.get(Meta, "last_updated")
        with_client = sum(1 for e in events if e.client)
        clusters = {e.cluster_id for e in events if e.cluster_id is not None}
        return {
            "last_updated": meta.value if meta else None,
            "total_events": len(events),
            "events_with_client": with_client,
            "incident_clusters": len(clusters),
        }
