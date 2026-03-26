#!/usr/bin/env python3
"""Process Safety News Monitor — scans global news for process safety events,
cross-references against client list (from Egnyte) and optionally Zoho CRM,
and maintains a JSON data file for the GitHub Pages dashboard."""

import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import config
from client_matcher import find_client_match, load_clients
from email_sender import send_report
from news_sources import NewsArticle, fetch_all_news
from report import generate_html_report
from zoho_client import ZohoClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "docs", "data")
EVENTS_FILE = os.path.join(DATA_DIR, "events.json")
MAX_EVENTS = 2000


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/").lower()


def _article_to_dict(article: NewsArticle, client_match: str | None) -> dict:
    return {
        "title": article.title,
        "url": article.url,
        "source": article.source,
        "date": article.date.isoformat() if article.date else None,
        "country": article.country,
        "keywords": article.keywords_matched,
        "client": client_match,
        "description": article.description,
    }


def load_existing_events() -> list[dict]:
    if not os.path.exists(EVENTS_FILE):
        return []
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def merge_events(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge new events into existing, deduplicating by URL. Keep most recent first."""
    seen: set[str] = set()
    merged: list[dict] = []

    # New events take priority (they may have updated client info)
    for event in new + existing:
        norm = _normalize_url(event.get("url", ""))
        if norm and norm not in seen:
            seen.add(norm)
            merged.append(event)

    # Sort by date descending
    merged.sort(key=lambda e: e.get("date") or "", reverse=True)

    # Cap at MAX_EVENTS
    return merged[:MAX_EVENTS]


def save_events(events: list[dict]):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2, ensure_ascii=False)


def main():
    logger.info("Starting Process Safety News Monitor (lookback=%dh)", config.LOOKBACK_HOURS)

    # 1. Fetch news
    articles = fetch_all_news()
    logger.info("Found %d articles matching keywords", len(articles))

    if not articles:
        logger.info("No articles found — nothing to report")
        return

    # 2. Check client list (from Egnyte project folders)
    clients = load_clients()
    client_matches: dict[str, str | None] = {}

    if clients:
        logger.info("Checking %d client names against headlines + descriptions", len(clients))
        for article in articles:
            # Check title first, then description
            search_text = article.title
            if article.description:
                search_text += " " + article.description
            match = find_client_match(search_text, clients)
            client_matches[article.url] = match
    else:
        logger.info("No client list found — skipping client matching")
        client_matches = {a.url: None for a in articles}

    # 2b. Also check Zoho CRM if configured
    zoho = ZohoClient()
    if zoho.configured:
        logger.info("Zoho CRM configured — checking for additional matches")
        try:
            for article in articles:
                if client_matches.get(article.url) is None:
                    match = zoho.find_matching_account(article.title)
                    if match:
                        client_matches[article.url] = match
        except Exception as e:
            logger.error("Zoho CRM check failed: %s", e)

    hits = sum(1 for v in client_matches.values() if v)
    logger.info("Client matches: %d out of %d articles", hits, len(articles))

    # 3. Update JSON data file for dashboard
    new_events = [
        _article_to_dict(a, client_matches.get(a.url))
        for a in articles
    ]
    existing_events = load_existing_events()

    # Re-match existing events against client list (picks up title+description matches)
    if clients:
        rematch_count = 0
        for event in existing_events:
            search_text = event.get("title", "")
            if event.get("description"):
                search_text += " " + event["description"]
            match = find_client_match(search_text, clients)
            if match and not event.get("client"):
                event["client"] = match
                rematch_count += 1
        if rematch_count:
            logger.info("Re-matched %d existing events to clients", rematch_count)

    merged = merge_events(existing_events, new_events)
    save_events(merged)
    logger.info(
        "Events data updated: %d new, %d total (was %d)",
        len(new_events), len(merged), len(existing_events),
    )

    # 4. Generate HTML email report + send (optional)
    html = generate_html_report(articles, client_matches, config.LOOKBACK_HOURS)

    report_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"report_{timestamp}.html")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("Report saved to %s", report_path)

    period = f"{config.LOOKBACK_HOURS}h" if config.LOOKBACK_HOURS <= 48 else f"{config.LOOKBACK_HOURS // 24}d"
    subject = f"Process Safety Monitor: {len(articles)} events ({period})"

    if send_report(subject, html):
        logger.info("Email digest sent successfully")
    else:
        logger.warning("Email not sent (check SMTP config). Report saved to: %s", report_path)

    logger.info("Done")


if __name__ == "__main__":
    main()
