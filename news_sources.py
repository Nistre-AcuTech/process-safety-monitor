import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse, quote

import feedparser
import requests

import config

logger = logging.getLogger(__name__)


@dataclass
class NewsArticle:
    title: str
    url: str
    source: str
    date: datetime | None
    country: str
    keywords_matched: list[str] = field(default_factory=list)


def _normalize_url(url: str) -> str:
    """Strip query params and fragments for deduplication."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/").lower()


def _match_keywords(text: str) -> list[str]:
    """Return list of keywords found in text (case-insensitive)."""
    text_lower = text.lower()
    return [kw for kw in config.KEYWORDS if kw.lower() in text_lower]


def fetch_gdelt(lookback_hours: int) -> list[NewsArticle]:
    """Fetch articles from GDELT DOC API."""
    query_parts = []
    for kw in config.KEYWORDS:
        if " " in kw:
            query_parts.append(f'"{kw}"')
        else:
            query_parts.append(kw)
    query = "(" + " OR ".join(query_parts) + ")"

    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "timespan": f"{lookback_hours}h",
        "maxrecords": config.GDELT_MAX_RECORDS,
        "sort": "datedesc",
    }

    for attempt in range(3):
        try:
            resp = requests.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params=params,
                timeout=30,
            )
            if resp.status_code == 429:
                logger.warning("GDELT rate limited, waiting %ds (attempt %d/3)", 6 * (attempt + 1), attempt + 1)
                time.sleep(6 * (attempt + 1))
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            logger.error("GDELT request failed: %s", e)
            return []
    else:
        logger.error("GDELT rate limit exceeded after 3 attempts")
        return []

    try:
        data = resp.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        logger.error("GDELT returned non-JSON response: %s", resp.text[:200])
        return []
    articles_data = data.get("articles", [])

    articles = []
    for item in articles_data:
        title = item.get("title", "")
        matched = _match_keywords(title)
        if not matched:
            continue

        date = None
        date_str = item.get("seendate", "")
        if date_str:
            try:
                date = datetime.strptime(date_str, "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                pass

        articles.append(
            NewsArticle(
                title=title,
                url=item.get("url", ""),
                source=item.get("domain", ""),
                date=date,
                country=item.get("sourcecountry", ""),
                keywords_matched=matched,
            )
        )

    logger.info("GDELT returned %d articles (%d after keyword filter)", len(articles_data), len(articles))
    return articles


def fetch_google_news(lookback_hours: int) -> list[NewsArticle]:
    """Fetch articles from Google News RSS."""
    # Build a query with the most distinctive keywords (avoid overly generic ones)
    priority_keywords = [
        "explosion", "detonation", "toxic", "vapor cloud", "hazmat",
        "chemical spill", "refinery fire", "chemical leak",
    ]
    query = " OR ".join(
        f'"{kw}"' if " " in kw else kw for kw in priority_keywords
    )
    encoded_query = quote(query)

    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en&gl=US&ceid=US:en"

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.error("Google News RSS fetch failed: %s", e)
        return []

    articles = []
    for entry in feed.entries:
        title = entry.get("title", "")
        matched = _match_keywords(title)
        if not matched:
            continue

        date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass

        source = entry.get("source", {}).get("title", "") if hasattr(entry, "source") else ""

        articles.append(
            NewsArticle(
                title=title,
                url=entry.get("link", ""),
                source=source,
                date=date,
                country="",  # Google News RSS doesn't provide country
                keywords_matched=matched,
            )
        )

    logger.info("Google News returned %d entries (%d after keyword filter)", len(feed.entries), len(articles))
    return articles


def fetch_all_news(lookback_hours: int | None = None) -> list[NewsArticle]:
    """Fetch from all sources, deduplicate, and sort by date."""
    if lookback_hours is None:
        lookback_hours = config.LOOKBACK_HOURS

    gdelt_articles = fetch_gdelt(lookback_hours)
    google_articles = fetch_google_news(lookback_hours)

    # Deduplicate by normalized URL
    seen_urls: set[str] = set()
    unique: list[NewsArticle] = []

    for article in gdelt_articles + google_articles:
        norm = _normalize_url(article.url)
        if norm not in seen_urls:
            seen_urls.add(norm)
            unique.append(article)

    # Sort by date descending (None dates last)
    unique.sort(key=lambda a: a.date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    logger.info("Total unique articles: %d", len(unique))
    return unique
