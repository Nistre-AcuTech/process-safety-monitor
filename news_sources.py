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
    "chemical spill", "chemical leak", "oil spill",
    "pipeline leak", "toxic release", "hazardous release", "chemical release",
    "refinery explosion", "dust explosion", "vapor cloud explosion",
    "toxic cloud", "vapor cloud", "BLEVE",
    "refinery incident", "plant incident", "industrial incident",
    "process safety", "chemical plant", "shelter in place",
    "CSB investigation", "OSHA citation", "OSHA fine", "EPA violation",
}

# Generic keywords that need industrial context to be relevant
_WEAK_KEYWORDS = {"explosion", "detonation", "hazmat", "gas leak"}

# Context words that confirm an article is about industrial/process safety
_INDUSTRY_CONTEXT = {
    "plant", "refinery", "factory", "facility", "pipeline", "terminal",
    "chemical", "industrial", "warehouse", "storage", "tank", "reactor",
    "petrochemical", "manufacturing", "processing", "osha", "epa",
    "hazardous", "flammable", "combustible",
    "shelter in place", "workers", "injuries",
    "spill", "release", "emission",
}

# Title patterns that indicate non-process-safety articles — always exclude
_EXCLUDE_PATTERNS = [
    # Residential / domestic
    "inside an apartment", "inside apartment", "inside his home",
    "inside their apartment", "inside her home", "inside a property",
    "kitchen fire", "stove",
    "home evacuation", "sewer smell", "suffocation",
    # Traffic / transport accidents (not process safety)
    "big-rig", "big rig", "truck crash", "highway crash", "traffic accident",
    "collision on", "crash on i-", "crash on us-",
    # Exercises / drills / training (not actual incidents)
    "exercise", "drill", "rehearse", "rehearsal", "training scenario",
    "preparing for upcoming",
    # Non-industrial
    "homeless", "encampment", "storm drain",
    "missing ashes", "teddy bears",
    # Historical / remembrance (not current events)
    "nurses remember", "anniversary of",
    # Regulatory / investment news (not incidents)
    "new rules for", "neue regeln", "invests in safety",
    "new regulations", "schulung",
    # Residential / non-industrial gas leaks (caught via translated titles)
    "gas leak in house", "gas leak at home", "gas leak in building",
    "gas leak in school", "gas leak in hospital",
    "fire breaks out in house", "killed at home by a gas",
    "farmhouse explodes",
    "residential area", "residential buildings",
    "petrol station", "gas station",
    "daycare", "kindergarten",
]


def _match_keywords(text: str) -> list[str]:
    """Return list of keywords found in text (case-insensitive).

    Strong keywords match directly. Weak keywords (like bare 'explosion',
    'hazmat', 'gas leak') only match if industrial context words are also
    present. Articles matching exclude patterns are always rejected.
    """
    text_lower = text.lower()

    # Check exclude patterns first
    if any(pat in text_lower for pat in _EXCLUDE_PATTERNS):
        return []

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


# US states for location detection
_US_STATES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
}

_US_STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}

_ABBREV_TO_STATE = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

# Countries commonly in process safety news
_COUNTRIES = {
    "United States", "United Kingdom", "Canada", "Australia", "Germany",
    "France", "India", "China", "Japan", "South Korea", "Brazil", "Mexico",
    "Nigeria", "Saudi Arabia", "Russia", "Indonesia", "Netherlands",
    "Belgium", "Italy", "Spain", "Norway", "Sweden", "Singapore",
    "Malaysia", "Thailand", "Qatar", "Kuwait", "Iraq", "Iran",
    "United Arab Emirates", "UAE", "Bahrain", "Oman", "Egypt", "Jordan",
    "Abu Dhabi", "Dubai",
}


def _detect_location(title: str, text: str) -> str:
    """Try to detect country/state from title and article text."""
    # Check title first (most reliable), then first 500 chars of text
    search_text = title + " " + text[:500]

    # Check for US state names (full names)
    for state in sorted(_US_STATES, key=len, reverse=True):
        pattern = r'\b' + re.escape(state) + r'\b'
        if re.search(pattern, search_text):
            return f"United States ({state})"

    # Check for US state abbreviations like "Port Arthur, TX" or "LUBBOCK, Texas"
    abbrev_match = re.search(r',\s*([A-Z]{2})\b', search_text)
    if abbrev_match and abbrev_match.group(1) in _US_STATE_ABBREVS:
        state = _ABBREV_TO_STATE[abbrev_match.group(1)]
        return f"United States ({state})"

    # Check for country names
    for country in sorted(_COUNTRIES, key=len, reverse=True):
        pattern = r'\b' + re.escape(country) + r'\b'
        if re.search(pattern, search_text, re.IGNORECASE):
            return country

    return ""


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
            # Detect location — override if we find something more specific
            detected = _detect_location(article.title, text or "")
            if detected:
                article.country = detected
            elif not article.country:
                article.country = ""
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


_DEFAULT_GOOGLE_KEYWORDS = [
    "refinery explosion", "chemical plant explosion", "industrial explosion",
    "refinery fire", "chemical fire", "chemical spill", "chemical leak",
    "vapor cloud", "hazmat", "shelter in place",
    "process safety", "OSHA fine", "CSB investigation",
]


# Non-English exclude patterns (translated equivalents of _EXCLUDE_PATTERNS)
_NON_EN_EXCLUDE_PATTERNS = [
    # Dutch — residential/domestic
    "woning", "appartement", "keuken", "riool",
    # Dutch — exercises
    "oefening",
    # German — residential/domestic
    "wohnung", "küche",
    # German — exercises/training
    "übung", "schulung",
    # German — traffic only
    "verkehr", "autobahn",
    # Italian — residential/domestic/non-industrial
    "appartamento", "cucina", "in casa", "palazzo", "palazzina",
    "scuola", "ospedale", "cascina", "condominio",
    "bombola", "distributore di carburante", "teatro",
    # Dutch — residential areas
    "woonwijk", "woonkern",
    # German — residential areas
    "wohngebiet", "wohnhäuser",
    # Arabic — domestic/residential
    "شقة", "منزل", "مطبخ",     # apartment, home, kitchen
]


def _match_keywords_custom(text: str, keywords: list[str]) -> list[str]:
    """Match against a custom keyword list (for non-English feeds)."""
    text_lower = text.lower()
    # Check non-English exclude patterns
    if any(pat in text_lower for pat in _NON_EN_EXCLUDE_PATTERNS):
        return []
    return [kw for kw in keywords if kw.lower() in text_lower]


def fetch_google_news_region(
    lookback_hours: int,
    gl: str = "US",
    hl: str = "en",
    ceid: str = "US:en",
    keywords_override: list[str] | None = None,
    label: str = "United States",
) -> list[NewsArticle]:
    """Fetch articles from Google News RSS for a specific region."""
    keywords = keywords_override or _DEFAULT_GOOGLE_KEYWORDS
    query = " OR ".join(
        f'"{kw}"' if " " in kw else kw for kw in keywords
    )
    encoded_query = quote(query)

    url = f"https://news.google.com/rss/search?q={encoded_query}&hl={hl}&gl={gl}&ceid={ceid}"

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.error("Google News RSS (%s) fetch failed: %s", label, e)
        return []

    articles = []
    for entry in feed.entries:
        title = entry.get("title", "")
        if keywords_override:
            matched = _match_keywords_custom(title, keywords_override)
        else:
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
                country=label if keywords_override else "",  # Pre-populate for non-English
                keywords_matched=matched,
            )
        )

    logger.info("Google News (%s) returned %d entries (%d after filter)", label, len(feed.entries), len(articles))
    return articles


def fetch_direct_rss(feed_config: dict, lookback_hours: int) -> list[NewsArticle]:
    """Fetch from a direct RSS feed (BBC, France 24, etc.) and filter by keywords."""
    url = feed_config["url"]
    source_name = feed_config["source"]

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.error("Direct RSS (%s) fetch failed: %s", source_name, e)
        return []

    articles = []
    for entry in feed.entries:
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        matched = _match_keywords(title + " " + summary)
        if not matched:
            continue

        date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass

        articles.append(
            NewsArticle(
                title=title,
                url=entry.get("link", ""),
                source=source_name,
                date=date,
                country="",
                keywords_matched=matched,
                description=summary[:300] if summary else "",
            )
        )

    logger.info("Direct RSS (%s) returned %d entries (%d after filter)", source_name, len(feed.entries), len(articles))
    return articles


def fetch_all_news(lookback_hours: int | None = None) -> list[NewsArticle]:
    """Fetch from all sources, deduplicate, sort by date, and fetch descriptions."""
    if lookback_hours is None:
        lookback_hours = config.LOOKBACK_HOURS

    all_articles: list[NewsArticle] = []

    # 1. GDELT (global)
    all_articles.extend(fetch_gdelt(lookback_hours))

    # 2. Google News — all regional editions (concurrent)
    regions = getattr(config, "GOOGLE_NEWS_REGIONS", [
        {"gl": "US", "hl": "en", "ceid": "US:en", "label": "United States"},
    ])
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(
                fetch_google_news_region,
                lookback_hours,
                gl=r["gl"], hl=r["hl"], ceid=r["ceid"],
                keywords_override=r.get("keywords"),
                label=r["label"],
            ): r["label"]
            for r in regions
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                all_articles.extend(future.result())
            except Exception as e:
                logger.error("Google News (%s) failed: %s", label, e)

    # 3. Direct RSS feeds (BBC, France 24, Deutsche Welle, etc.)
    direct_feeds = getattr(config, "DIRECT_RSS_FEEDS", [])
    if direct_feeds:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(fetch_direct_rss, feed, lookback_hours): feed["source"]
                for feed in direct_feeds
            }
            for future in as_completed(futures):
                source = futures[future]
                try:
                    all_articles.extend(future.result())
                except Exception as e:
                    logger.error("Direct RSS (%s) failed: %s", source, e)

    # Deduplicate by normalized URL
    seen_urls: set[str] = set()
    unique: list[NewsArticle] = []

    for article in all_articles:
        norm = _normalize_url(article.url)
        if norm not in seen_urls:
            seen_urls.add(norm)
            unique.append(article)

    # Sort by date descending (None dates last)
    unique.sort(key=lambda a: a.date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    # Fetch full article text (for client matching + display snippets)
    fetch_article_texts(unique)

    logger.info("Total unique articles: %d (from %d raw)", len(unique), len(all_articles))
    return unique
