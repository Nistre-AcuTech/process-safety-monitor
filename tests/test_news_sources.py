"""Hermetic tests for keyword matching and the news-sitemap source.

No network. The sitemap fetcher is exercised against a canned XML payload with
`requests.get` monkeypatched.

Covers three changes made 2026-09-16:
  - _EXCLUDE_PATTERNS are matched as whole words, so "drill" stops swallowing
    every drilling-rig story,
  - _match_curated (event word + industrial context) for trade-press sources,
    and the guarantee that general feeds still use the strict matcher,
  - fetch_news_sitemap, plus (source, title) dedupe in fetch_all_news.

Run: pytest -q
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import news_sources as ns  # noqa: E402


# --------------------------------------------------------------------------
# The "drill" bug: exclude patterns must match whole words only.
# --------------------------------------------------------------------------
DRILLING_TITLES = [
    "Venezuela drilling rig incident leaves workers injured",
    "Offshore drilling company fined after crane boom collapse at platform",
    "Explosion aboard drillship injures two workers offshore",
    "Offshore drilling platform blowout prompts chemical release",
]


@pytest.mark.parametrize("title", DRILLING_TITLES)
def test_drilling_stories_are_not_excluded(title):
    """'drill' is in the exclude list for training drills, not for drilling."""
    assert not ns._EXCLUDE_RE.search(title), f"still excluded: {title}"


@pytest.mark.parametrize("title", [
    "Refinery runs emergency drill to test response",
    "Plant holds evacuation drills after audit",
    "Chemical plant explosion was part of a training scenario",
])
def test_actual_drills_and_exercises_are_still_excluded(title):
    assert ns._EXCLUDE_RE.search(title), f"should be excluded: {title}"


def test_exclude_still_catches_residential_phrases():
    """The original intent of the list is unchanged."""
    for title in [
        "Fire broke out inside an apartment in Leeds",
        "Kitchen fire damages home",
        "Gas leak in school prompts evacuation",
    ]:
        assert ns._EXCLUDE_RE.search(title), title


def test_non_english_excludes_remain_substring_matched():
    """German/Dutch compounds need substring matching — 'wohnung' must still
    reach 'Wohnungsbrand'. This is why _EXCLUDE_RE is English-only."""
    assert ns._match_keywords_custom(
        "Wohnungsbrand in Hamburg", ["Wohnungsbrand", "Chemiebrand"]
    ) == []


# --------------------------------------------------------------------------
# The curated matcher.
# --------------------------------------------------------------------------
# Real HazardEx headlines the strict bigram matcher dropped.
CURATED_SHOULD_MATCH = [
    "Molten metal fire kills eight at Visakhapatnam Steel Plant",
    "Esso fined 1m after major LPG leak at Fawley refinery",
    "CSB opens investigation into fatal chemical tank implosion at Washington paper mill",
    "Fire at Baku oil refinery extinguished after process unit release",
    "Two killed and nine injured in reactor blast at Telangana pharmaceutical unit",
    "US Steel Clairton coke oven gas explosion linked to valve failure",
    "Catastrophic chemical tank rupture under investigation at Washington pulp mill",
    "ExxonMobil fined 267 000 after five flammable hydrocarbon leaks at Fife UK plant",
    "Iraq hydrogen compressor fire at Baiji refinery",
]

# Real HazardEx headlines that are promos, adverts or commercial news.
CURATED_SHOULD_NOT_MATCH = [
    "Hazardex in the Regions 2026 Ellesmere Port Richard Hellebrand",
    "2 weeks to go Ellesmere Port 23 09 2026 Last free delegate tickets available",
    "Italian Saipem awarded 1 8bn Middle East offshore EPCI contract",
    "ADNOC approves 6 2bn Umm Shaif Gas Cap development",
    "Gland IP ratings",
    "Hot water circulation pumps for offshore economiser",
    "US and Saudi Arabia sign civilian nuclear cooperation agreement",
    "Awards Nominations now open Hazardex Live 2027",
]


@pytest.mark.parametrize("title", CURATED_SHOULD_MATCH)
def test_curated_matcher_catches_real_incidents(title):
    assert ns._match_curated(title), f"missed: {title}"


@pytest.mark.parametrize("title", CURATED_SHOULD_NOT_MATCH)
def test_curated_matcher_rejects_promos(title):
    assert ns._match_curated(title) == [], f"false positive: {title}"


def test_curated_requires_both_event_and_context():
    # Event word, no industrial context.
    assert ns._match_curated("Explosion reported downtown") == []
    # Industrial context, no event word.
    assert ns._match_curated("New refinery opens in Texas") == []
    # Both.
    assert ns._match_curated("Explosion at the refinery")


def test_curated_returns_terms_for_the_report():
    """main.py stores this list and report.py comma-joins it."""
    matched = ns._match_curated("Fire at Michigan power plant prompts emergency response")
    assert isinstance(matched, list)
    assert all(isinstance(m, str) for m in matched)
    assert "fire" in matched


def test_curated_matcher_would_false_positive_on_general_news():
    """Documents WHY curated matching is opt-in per feed.

    This is war reporting, and the curated matcher accepts it ("workers" +
    "fire"). It was one of 3 false positives it produced against 215 raw
    BBC / France 24 / DW / Al Jazeera / Gulf News entries, against 0 real
    articles recovered. General feeds must keep _match_keywords.
    """
    assert ns._match_curated("Ukraine's postal workers deliver lifeline under Russian fire")
    assert ns._match_keywords("Ukraine's postal workers deliver lifeline under Russian fire") == []


def test_general_feeds_do_not_opt_into_curated_matching():
    import config
    for feed in getattr(config, "DIRECT_RSS_FEEDS", []):
        assert not feed.get("curated"), f"{feed['source']} must not be curated"


# --------------------------------------------------------------------------
# fetch_news_sitemap
# --------------------------------------------------------------------------
def _sitemap(entries: list[tuple[str, str, str]]) -> bytes:
    """entries = [(loc, title, iso_date_or_empty)]"""
    items = []
    for loc, title, date in entries:
        date_el = f"<news:publication_date>{date}</news:publication_date>" if date else ""
        items.append(
            f"<url><loc>{loc}</loc><news:news>"
            f"<news:publication><news:name>HazardEx</news:name>"
            f"<news:language>en</news:language></news:publication>"
            f"{date_el}<news:title>{title}</news:title>"
            f"</news:news></url>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<urlset xmlns:news="http://www.google.com/schemas/sitemap-news/0.9" '
        'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(items)
        + "</urlset>"
    ).encode("utf-8")


class _FakeResp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


@pytest.fixture
def feed():
    return {
        "url": "https://example.test/news-sitemap.xml",
        "source": "HazardEx",
        "curated": True,
    }


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT00:00:00Z"
    )


def test_sitemap_parses_title_url_and_date(monkeypatch, feed):
    xml = _sitemap([
        ("https://example.test/article/1/fire.aspx",
         "Fire at Michigan power plant prompts emergency response", _iso(1)),
    ])
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: _FakeResp(xml))

    got = ns.fetch_news_sitemap(feed, lookback_hours=6)

    assert len(got) == 1
    art = got[0]
    assert art.url == "https://example.test/article/1/fire.aspx"
    assert art.title.startswith("Fire at Michigan power plant")
    assert art.source == "HazardEx"
    assert art.date is not None and art.date.tzinfo is not None
    assert art.keywords_matched


def test_sitemap_ignores_the_global_lookback(monkeypatch, feed):
    """Regression guard for a bug caught by the first live run.

    HazardEx's sitemap held nothing newer than 7 days old, so *any* recent-news
    window emptied it — a 7-day window returned 0 of 25 against the live feed.
    The source would have looked healthy and produced nothing forever. Sitemaps
    are self-limiting, so by default nothing is filtered on date.
    """
    xml = _sitemap([
        ("https://example.test/article/1/a.aspx",
         "Fire at Michigan power plant prompts emergency response", _iso(2)),
        ("https://example.test/article/2/b.aspx",
         "Esso fined 1m after major LPG leak at Fawley refinery", _iso(9)),
        ("https://example.test/article/3/c.aspx",
         "Molten metal fire kills eight at Visakhapatnam Steel Plant", _iso(36)),
    ])
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: _FakeResp(xml))

    assert len(ns.fetch_news_sitemap(feed, lookback_hours=6)) == 3


def test_sitemap_window_applies_only_when_the_feed_asks_for_one(monkeypatch, feed):
    xml = _sitemap([
        ("https://example.test/article/1/new.aspx",
         "Fire at Michigan power plant prompts emergency response", _iso(2)),
        ("https://example.test/article/2/old.aspx",
         "Esso fined 1m after major LPG leak at Fawley refinery", _iso(30)),
    ])
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: _FakeResp(xml))

    feed["lookback_hours"] = 24 * 7
    got = ns.fetch_news_sitemap(feed, lookback_hours=6)
    assert [a.url for a in got] == ["https://example.test/article/1/new.aspx"]


def test_sitemap_keeps_undated_entries(monkeypatch, feed):
    """Dropping them would silently lose articles if the field is ever absent."""
    xml = _sitemap([
        ("https://example.test/article/1/a.aspx",
         "Fire at Michigan power plant prompts emergency response", ""),
    ])
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: _FakeResp(xml))

    got = ns.fetch_news_sitemap(feed, lookback_hours=6)
    assert len(got) == 1 and got[0].date is None


def test_sitemap_applies_the_matcher(monkeypatch, feed):
    xml = _sitemap([
        ("https://example.test/article/1/promo.aspx",
         "Awards Nominations now open Hazardex Live 2027", _iso(1)),
        ("https://example.test/article/2/real.aspx",
         "Esso fined 1m after major LPG leak at Fawley refinery", _iso(1)),
    ])
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: _FakeResp(xml))

    got = ns.fetch_news_sitemap(feed, lookback_hours=6)
    assert [a.url for a in got] == ["https://example.test/article/2/real.aspx"]


def test_sitemap_network_failure_returns_empty(monkeypatch, feed):
    def boom(*a, **k):
        raise ns.requests.RequestException("nope")
    monkeypatch.setattr(ns.requests, "get", boom)

    assert ns.fetch_news_sitemap(feed, lookback_hours=6) == []


def test_sitemap_malformed_xml_returns_empty(monkeypatch, feed):
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: _FakeResp(b"<not xml"))

    assert ns.fetch_news_sitemap(feed, lookback_hours=6) == []


def test_sitemap_ignores_plain_urlset_entries(monkeypatch, feed):
    """A regular sitemap.xml has no <news:news>; those rows carry no title."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://example.test/a.aspx</loc></url>"
        "</urlset>"
    ).encode()
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: _FakeResp(xml))

    assert ns.fetch_news_sitemap(feed, lookback_hours=6) == []


# --------------------------------------------------------------------------
# Dedupe
# --------------------------------------------------------------------------
def test_same_story_under_two_article_ids_is_deduped(monkeypatch):
    """HazardEx publishes duplicates under consecutive ids (223962/223963)."""
    dupe_title = "Esso fined 1m after major LPG leak at Fawley refinery"
    xml = _sitemap([
        ("https://example.test/article/223963/a.aspx", dupe_title, _iso(1)),
        ("https://example.test/article/223962/a.aspx", dupe_title, _iso(1)),
    ])
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: _FakeResp(xml))
    monkeypatch.setattr(ns, "fetch_gdelt", lambda h: [])
    monkeypatch.setattr(ns, "fetch_google_news_region", lambda *a, **k: [])
    monkeypatch.setattr(ns, "fetch_direct_rss", lambda *a, **k: [])
    monkeypatch.setattr(ns, "fetch_article_texts", lambda arts, **k: None)
    monkeypatch.setattr(ns.config, "DIRECT_RSS_FEEDS", [])
    monkeypatch.setattr(ns.config, "GOOGLE_NEWS_REGIONS", [])
    monkeypatch.setattr(ns.config, "NEWS_SITEMAP_FEEDS", [{
        "url": "https://example.test/news-sitemap.xml",
        "source": "HazardEx", "curated": True,
    }])

    got = ns.fetch_all_news(lookback_hours=6)
    assert len(got) == 1


def test_same_headline_from_two_outlets_is_kept(monkeypatch):
    """Title dedupe is scoped per-source; clustering handles cross-outlet."""
    title = "Esso fined 1m after major LPG leak at Fawley refinery"
    a = ns.NewsArticle(title=title, url="https://a.test/1", source="BBC",
                       date=None, country="")
    b = ns.NewsArticle(title=title, url="https://b.test/1", source="Reuters",
                       date=None, country="")
    monkeypatch.setattr(ns, "fetch_gdelt", lambda h: [a, b])
    monkeypatch.setattr(ns, "fetch_article_texts", lambda arts, **k: None)
    monkeypatch.setattr(ns.config, "DIRECT_RSS_FEEDS", [])
    monkeypatch.setattr(ns.config, "GOOGLE_NEWS_REGIONS", [])
    monkeypatch.setattr(ns.config, "NEWS_SITEMAP_FEEDS", [])

    assert len(ns.fetch_all_news(lookback_hours=6)) == 2
