import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse, quote

import feedparser
import requests
import trafilatura
from googlenewsdecoder import new_decoderv1

import config

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


@dataclass
class NewsArticle:
    title: str
    url: str
    source: str
    date: datetime | None
    country: str
    keywords_matched: list[str] = field(default_factory=list)
    description: str = ""
    full_text: str = ""


def _normalize_url(url: str) -> str:
    """Strip query params and fragments for deduplication."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/").lower()


# Keywords that are specific enough to stand alone
_STRONG_KEYWORDS = {
    "refinery fire", "chemical fire", "industrial fire", "plant fire",
    "factory fire", "tank fire", "pipeline fire", "warehouse fire",
    "chemical spill", "chemical leak", "gas leak", "oil spill",
    "pipeline leak", "toxic release", "hazardous release", "chemical release",
    "refinery explosion", "dust explosion", "vapor cloud explosion",
    "hazmat", "toxic cloud", "vapor cloud", "BLEVE",
    "refinery incident", "plant incident", "industrial incident",
    "process safety", "chemical plant", "shelter in place",
    "CSB investigation", "OSHA citation", "OSHA fine", "EPA violation",
}

# Generic keywords that need industrial context to be relevant
_WEAK_KEYWORDS = {"explosion", "detonation"}

# Context words that confirm an article is about industrial/process safety
_INDUSTRY_CONTEXT = {
    "plant", "refinery", "factory", "facility", "pipeline", "terminal",
    "chemical", "industrial", "warehouse", "storage", "tank", "reactor",
    "petrochemical", "manufacturing", "processing", "osha", "epa",
    "hazardous", "flammable", "combustible", "evacuate", "evacuation",
    "shelter in place", "workers", "injuries", "safety", "leak",
}


def _match_keywords(text: str) -> list[str]:
    """Return list of keywords found in text (case-insensitive).

    Strong keywords match directly. Weak keywords (like bare 'explosion')
    only match if industrial context words are also present in the title.
    """
    text_lower = text.lower()
    matched = []

    for kw in config.KEYWORDS:
        if kw.lower() in text_lower:
            matched.append(kw)

    if not matched:
        return []

    # If all matches are weak keywords, require industrial context
    has_strong = any(m.lower() in _STRONG_KEYWORDS for m in matched)
    if not has_strong:
        has_context = any(ctx in text_lower for ctx in _INDUSTRY_CONTEXT)
        if not has_context:
            return []

    return matched


def _resolve_google_news_url(url: str) -> str:
    """Resolve a Google News redirect URL to the actual article URL."""
    if "news.google.com" not in url:
        return url
    try:
        result = new_decoderv1(url)
        if result.get("status") and result.get("decoded_url"):
            return result["decoded_url"]
    except Exception:
        pass
    return url


def _extract_article_text(url: str) -> str:
    """Fetch a URL and extract the main article text using trafilatura."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
        return text or ""
    except Exception:
        return ""


def _resolve_and_extract(article: NewsArticle) -> tuple[str, str]:
    """Resolve Google News URL if needed, then extract article text."""
    real_url = _resolve_google_news_url(article.url)
    text = _extract_article_text(real_url)
    return real_url, text


def fetch_article_texts(articles: list[NewsArticle], max_workers: int = 8):
    """Fetch full article text for all articles concurrently.

    Resolves Google News redirect URLs to actual article URLs.
    Stores full text in article.full_text for client matching,
    and a short snippet in article.description for display.
    """
    to_fetch = [a for a in articles if not a.description]
    logger.info("Fetching article text for %d articles...", len(to_fetch))
    count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_article = {
            pool.submit(_resolve_and_extract, a): a
            for a in to_fetch
        }
        for future in as_completed(future_to_article):
            article = future_to_article[future]
            try:
                real_url, text = future.result()
            except Exception:
                continue
            # Update URL to the real article URL
            if real_url != article.url:
                article.url = real_url
            if text:
                article.full_text = text
                # First 300 chars as display snippet, break at sentence/word
                snippet = text[:500]
                # Try to break at a sentence boundary
                for end in ('.', '!', '?'):
                    last = snippet[:300].rfind(end)
                    if last > 100:
                        snippet = snippet[:last + 1]
                        break
                else:
                    # Break at word boundary
                    snippet = snippet[:300]
                    last_space = snippet.rfind(' ')
                    if last_space > 100:
                        snippet = snippet[:last_space] + '...'
                article.description = snippet
                count += 1

    logger.info("Extracted text from %d of %d articles", count, len(to_fetch))


def fetch_gdelt(lookback_hours: int) -> list[NewsArticle]:
    """Fetch articles from GDELT DOC API."""
    # Use a shorter keyword set for GDELT (has query length limit)
    gdelt_keywords = [
        "refinery explosion", "chemical plant", "chemical spill",
        "chemical leak", "industrial explosion", "hazmat",
        "vapor cloud", "refinery fire", "process safety",
        "shelter in place", "OSHA fine", "industrial fire",
    ]
    query_parts = []
    for kw in gdelt_keywords:
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
    # Build a query with the most distinctive process-safety keywords
    priority_keywords = [
        "refinery explosion", "chemical plant explosion", "industrial explosion",
        "refinery fire", "chemical fire", "chemical spill", "chemical leak",
        "vapor cloud", "hazmat", "shelter in place",
        "process safety", "OSHA fine", "CSB investigation",
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
    """Fetch from all sources, deduplicate, sort by date, and fetch descriptions."""
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

    # Fetch full article text (for client matching + display snippets)
    fetch_article_texts(unique)

    logger.info("Total unique articles: %d", len(unique))
    return unique
