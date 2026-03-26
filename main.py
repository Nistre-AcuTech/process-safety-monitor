#!/usr/bin/env python3
"""Process Safety News Monitor — scans global news for process safety events,
cross-references against Zoho CRM, and maintains a JSON data file for the
GitHub Pages dashboard."""

import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import config
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


def _article_to_dict(article: NewsArticle, zoho_account: str | None) -> dict:
    return {
        "title": article.title,
        "url": article.url,
        "source": article.source,
        "date": article.date.isoformat() if article.date else None,
        "country": article.country,
        "keywords": article.keywords_matched,
        "zoho_account": zoho_account,
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

    # New events take priority (they may have updated CRM info)
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

    # 2. Check Zoho CRM for matching accounts
    zoho = ZohoClient()
    zoho_matches: dict[str, str | None] = {}

    if zoho.configured:
        logger.info("Zoho CRM configured — checking for matching accounts")
        try:
            for article in articles:
                match = zoho.find_matching_account(article.title)
                zoho_matches[article.url] = match
        except Exception as e:
            logger.error("Zoho CRM check failed: %s", e)
            zoho_matches = {a.url: None for a in articles}
    else:
        logger.info("Zoho CRM not configured — skipping account check")
        zoho_matches = {a.url: None for a in articles}

    crm_hits = sum(1 for v in zoho_matches.values() if v)
    logger.info("CRM matches: %d out of %d articles", crm_hits, len(articles))

    # 3. Update JSON data file for dashboard
    new_events = [
        _article_to_dict(a, zoho_matches.get(a.url))
        for a in articles
    ]
    existing_events = load_existing_events()
    merged = merge_events(existing_events, new_events)
    save_events(merged)
    logger.info(
        "Events data updated: %d new, %d total (was %d)",
        len(new_events), len(merged), len(existing_events),
    )

    # 4. Generate HTML email report + send (optional)
    html = generate_html_report(articles, zoho_matches, config.LOOKBACK_HOURS)

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
