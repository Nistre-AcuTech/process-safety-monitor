import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, quote
from xml.etree import ElementTree

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
    "kitchen fire", "stove", "stovetop",
    "home evacuation", "sewer smell", "suffocation",
    # Traffic / transport accidents (not process safety)
    "big-rig", "big rig", "truck crash", "highway crash", "traffic accident",
    "collision on", "crash on i-", "crash on us-",
    # Exercises / drills / training (not actual incidents)
    # NB: these are matched as whole words (see _EXCLUDE_RE), so plurals and
    # variants have to be spelled out — "drill" must not reach "drilling".
    "exercise", "exercises", "drill", "drills", "rehearse", "rehearsal",
    "training scenario", "preparing for upcoming",
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


# The English exclude patterns are matched as WHOLE WORDS, not bare substrings.
#
# "drill" is why. It is in the list for emergency/training drills, but a bare
# `pat in text` also swallowed every "drilling rig", "drillship" and "offshore
# drilling platform" story — before keyword matching even ran, so the drop was
# invisible in the logs. 51 titles in HazardEx's archive alone, among them
# "Offshore drilling company fined after crane boom collapse".
#
# Deliberately NOT applied to _NON_EN_EXCLUDE_PATTERNS below: Dutch and German
# compound nouns rely on substring matching ("wohnung" has to reach
# "Wohnungsbrand"), so those stay as-is.
_EXCLUDE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in _EXCLUDE_PATTERNS) + r")\b",
    re.IGNORECASE,
)


def _match_keywords(text: str) -> list[str]:
    """Return list of keywords found in text (case-insensitive).

    Strong keywords match directly. Weak keywords (like bare 'explosion',
    'hazmat', 'gas leak') only match if industrial context words are also
    present. Articles matching exclude patterns are always rejected.
    """
    text_lower = text.lower()

    # Check exclude patterns first
    if _EXCLUDE_RE.search(text_lower):
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


# --------------------------------------------------------------------------
# Matching for CURATED sources (process-safety trade press).
#
# config.KEYWORDS is a list of rigid adjacent pairs — "refinery fire", "plant
# fire", "CSB investigation". Real headlines don't co-operate: "Fire at Baku oil
# refinery", "Fire at Michigan power plant" and "CSB opens investigation" each
# contain both words and match none of the pairs. Measured against 101
# hand-labelled HazardEx articles, _match_keywords found 38 of 72 relevant ones
# (53%), and 36 of those 38 matched on the bare word "explosion" — in practice
# it is an explosion detector. Fires, leaks, ruptures, blasts and implosions
# went through untouched, including two CSB investigations and a steel plant
# fire that killed eight.
#
# So for sources that only ever publish process-safety news, match on
# co-occurrence instead: any hazard EVENT word plus any INDUSTRIAL CONTEXT word,
# anywhere in the title. That takes recall to 96% (69/72) at 100% precision.
#
# This is NOT safe for general news feeds, and is not used for them. On 215 raw
# BBC / France 24 / DW / Al Jazeera / Gulf News entries it admitted 3 items the
# strict matcher rejected, and all 3 were false positives ("postal workers
# deliver lifeline under Russian fire", two Sudanese gold-mine collapses).
# General feeds keep _match_keywords; only feeds flagged `"curated": True` use
# this.
_CURATED_EVENT_TERMS = [
    r"explosion\w*", r"blast\w*", r"detonat\w*", r"implosion\w*", r"implode\w*",
    r"fire", r"fires", r"blaze\w*",
    r"leak\w*", r"spill\w*", r"release\w*", r"rupture\w*", r"burst",
    r"hazmat", r"toxic cloud", r"vapou?r cloud", r"bleve", r"runaway reaction",
    r"incident\w*", r"accident\w*", r"failure\w*", r"collapse\w*",
    r"evacuat\w*", r"shelter in place", r"contaminat\w*",
    # Enforcement / investigation outcomes — the aftermath of an incident is
    # every bit as reportable as the incident ("Esso fined £1m after LPG leak").
    r"fined", r"fine[sd]?\b", r"penalt\w*", r"prosecut\w*", r"citation\w*",
    r"violation\w*", r"probe", r"investigation\w*", r"enforcement",
    r"guilty", r"verdict", r"sentenc\w*", r"charged",
]
_CURATED_CONTEXT_TERMS = [
    r"plant\w*", r"refiner\w*", r"factor\w*", r"facilit\w*", r"pipeline\w*",
    r"terminal\w*", r"chemical\w*", r"petrochemical\w*", r"industrial",
    r"warehouse\w*", r"storage", r"tank\w*", r"reactor\w*", r"vessel\w*",
    r"mill\w*", r"mine\w*", r"smelter\w*", r"foundr\w*", r"furnace\w*",
    r"oven\w*", r"shipyard\w*", r"depot\w*", r"silo\w*", r"compressor\w*",
    r"electroly\w*", r"rig\b", r"rigs\b", r"drilling", r"offshore", r"well\w*",
    r"port\w*", r"harbour", r"dock\w*",
    r"processing", r"manufactur\w*", r"production", r"process unit",
    r"steel\w*", r"coal", r"nuclear", r"pharmaceutical\w*", r"waste",
    r"lng", r"lpg", r"hydrogen", r"ammonia", r"propane", r"methane",
    r"butane", r"ethylene", r"chlorine", r"hydrocarbon\w*", r"crude",
    r"fuel\w*", r"oil\b", r"gas\b", r"petroleum", r"solvent\w*",
    r"hazardous", r"flammable", r"combustible", r"explosive\w*", r"corros\w*",
    r"osha", r"epa\b", r"hse\b", r"csb\b", r"onr\b", r"atex", r"iecex",
    r"worker\w*", r"employee\w*", r"operator\w*", r"contractor\w*",
    r"valve\w*", r"extraction", r"battery", r"energy",
]
_CURATED_EVENT_RE = re.compile(r"\b(?:" + "|".join(_CURATED_EVENT_TERMS) + r")", re.IGNORECASE)
_CURATED_CONTEXT_RE = re.compile(r"\b(?:" + "|".join(_CURATED_CONTEXT_TERMS) + r")", re.IGNORECASE)


def _match_curated(text: str) -> list[str]:
    """Match a title from a curated process-safety source.

    Requires a hazard event word AND an industrial context word. Returns the
    terms that fired (event terms first) so the dashboard and email report can
    show why an article was kept, same as _match_keywords' return value.
    """
    if _EXCLUDE_RE.search(text):
        return []

    events = sorted({m.group(0).lower() for m in _CURATED_EVENT_RE.finditer(text)})
    if not events:
        return []

    context = sorted({m.group(0).lower() for m in _CURATED_CONTEXT_RE.finditer(text)})
    if not context:
        return []

    # Cap it — these are shown as a comma-joined string in the report.
    return events[:3] + context[:3]


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
    "woning", "woonhuis", "appartement", "keuken", "riool",
    "flatgebouw", "seniorenflat", "portiekflat",
    # Dutch — vehicle/transport fires
    "autobrand", "brand in auto", "brand in bus",
    "brand in vrachtwagen", "voertuigbrand",
    # Dutch — residential fires
    "woningbrand", "flatbrand", "schuurbrand", "zolderbrand",
    "brand in schuur", "brand in flat", "brand in slaapkamer",
    # Dutch — dumpster/container fires
    "containerbrand", "afvalbrand", "prullenbak",
    # Dutch — non-industrial leaks
    "waterlek", "waterlekkage", "datalek",
    # Dutch — fireworks / military
    "vuurwerk",
    # Dutch — traffic
    "verkeersongeval", "aanrijding",
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
    """Fetch from a direct RSS feed (BBC, France 24, etc.) and filter by keywords.

    A feed marked `"curated": True` is a process-safety trade publication whose
    whole output is on-topic, so it gets the looser co-occurrence matcher.
    """
    url = feed_config["url"]
    source_name = feed_config["source"]
    matcher = _match_curated if feed_config.get("curated") else _match_keywords

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.error("Direct RSS (%s) fetch failed: %s", source_name, e)
        return []

    articles = []
    for entry in feed.entries:
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        matched = matcher(title + " " + summary)
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


_SITEMAP_NS = {
    "s": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}


def fetch_news_sitemap(feed_config: dict, lookback_hours: int) -> list[NewsArticle]:
    """Fetch from a Google News sitemap (`<news:news>` entries).

    Some trade publications run no RSS feed at all but do publish a news
    sitemap for Google, which carries exactly what we need: canonical URL,
    title and publication date. HazardEx is the case this was written for —
    /rss, /feed and /rss.xml all 404 and the homepage declares no alternate
    link, but /news-sitemap.xml lists the last ~25 articles.

    A news sitemap is NOT time-filtered by default, and the global
    LOOKBACK_HOURS is deliberately ignored. Two reasons, both measured against
    the live HazardEx feed:

      - It is already self-limiting — 25 entries, and the publisher decides
        what is in it. There is nothing to protect against.
      - Its contents run *old*. On 2026-09-16 the 25 entries spanned 7 to 36
        days, the freshest being a week back, because HazardEx publishes in
        weekly batches and the sitemap lags. Any window short enough to feel
        like "recent news" returns zero — a 7-day window returned zero on the
        first live run — and the source would look healthy while producing
        nothing, which is precisely how the 8-week outage went unnoticed.

    Re-seeing an article every run is harmless: fetch_all_news dedupes on URL
    and title, and merge_events dedupes again against what is already stored.
    Set `lookback_hours` on the feed config only if a publisher's sitemap is
    genuinely too long.
    """
    url = feed_config["url"]
    source_name = feed_config["source"]
    matcher = _match_curated if feed_config.get("curated") else _match_keywords
    window_hours = feed_config.get("lookback_hours")

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
    except (requests.RequestException, ElementTree.ParseError) as e:
        logger.error("News sitemap (%s) fetch failed: %s", source_name, e)
        return []

    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=window_hours)
        if window_hours
        else None
    )
    articles = []
    total = 0

    for url_el in root.findall("s:url", _SITEMAP_NS):
        news_el = url_el.find("news:news", _SITEMAP_NS)
        if news_el is None:
            continue
        total += 1

        loc = url_el.findtext("s:loc", default="", namespaces=_SITEMAP_NS).strip()
        title = news_el.findtext("news:title", default="", namespaces=_SITEMAP_NS).strip()
        if not loc or not title:
            continue

        date = None
        raw_date = news_el.findtext(
            "news:publication_date", default="", namespaces=_SITEMAP_NS
        ).strip()
        if raw_date:
            try:
                date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
            except ValueError:
                logger.debug("News sitemap (%s): unparseable date %r", source_name, raw_date)

        # Undated entries are kept — dropping them would silently lose articles
        # if a publisher ever omits the field.
        if cutoff is not None and date is not None and date < cutoff:
            continue

        matched = matcher(title)
        if not matched:
            continue

        articles.append(
            NewsArticle(
                title=title,
                url=loc,
                source=source_name,
                date=date,
                country="",
                keywords_matched=matched,
            )
        )

    logger.info(
        "News sitemap (%s) returned %d entries (%d after filter, window=%s)",
        source_name, total, len(articles),
        f"{window_hours}h" if window_hours else "none",
    )
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

    # 4. News sitemaps (trade press with no RSS feed — e.g. HazardEx)
    sitemap_feeds = getattr(config, "NEWS_SITEMAP_FEEDS", [])
    if sitemap_feeds:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(fetch_news_sitemap, feed, lookback_hours): feed["source"]
                for feed in sitemap_feeds
            }
            for future in as_completed(futures):
                source = futures[future]
                try:
                    all_articles.extend(future.result())
                except Exception as e:
                    logger.error("News sitemap (%s) failed: %s", source, e)

    # Deduplicate by normalized URL, then by title within a source.
    #
    # The title pass is for sitemaps: HazardEx republishes the same story under
    # a second article id (223962 and 223963 are both "2 weeks to go. Ellesmere
    # Port..."), so the URLs differ and URL dedupe alone lets both through.
    # Scoped to (source, title) deliberately — two outlets covering the same
    # incident are separate articles, and clustering already groups those.
    seen_urls: set[str] = set()
    seen_titles: set[tuple[str, str]] = set()
    unique: list[NewsArticle] = []

    for article in all_articles:
        norm = _normalize_url(article.url)
        if norm in seen_urls:
            continue
        title_key = (article.source.lower(), " ".join(article.title.lower().split()))
        if title_key[1] and title_key in seen_titles:
            continue
        seen_urls.add(norm)
        seen_titles.add(title_key)
        unique.append(article)

    # Sort by date descending (None dates last)
    unique.sort(key=lambda a: a.date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    # Fetch full article text (for client matching + display snippets)
    fetch_article_texts(unique)

    logger.info("Total unique articles: %d (from %d raw)", len(unique), len(all_articles))
    return unique
