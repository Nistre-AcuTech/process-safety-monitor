"""Hermetic tests for client matching.

Added 2026-09-16 after auditing what the matcher was actually tagging in prod:
clients.json is built by scraping Egnyte folder names, so administrative folders
("Technical", "Security", "Solar", "Cancelled", "Contract") became client names
and — because the search covers the full article body, not just the title —
tagged real incidents with a bogus client. "Technical" alone was on 31 events.

Run: pytest -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import client_matcher as cm  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    """The search table is cached at module level."""
    cm._SEARCH_TABLE_CACHE = None
    yield
    cm._SEARCH_TABLE_CACHE = None


# Real prod false positives, with the client each was wrongly tagged with.
PROD_FALSE_POSITIVES = [
    ("Iraq fuel depot fire injures 41 and destroys 38 tankers", "Technical"),
    ("Explosion at Turkish steel plant injures two workers", "Technical"),
    ("Pentagon on lockdown, hazmat crews rush in over hazardous materials incident", "Technical"),
    ("FBI serves search warrant at Southern California chemical plant", "Technical"),
]


@pytest.mark.parametrize("title,wrong_client", PROD_FALSE_POSITIVES)
def test_admin_folder_names_no_longer_match(title, wrong_client):
    assert cm.find_client_match(title) != wrong_client


@pytest.mark.parametrize("term", [
    "technical", "security", "solar", "contract", "cancelled",
    "checklists", "proposals", "savage", "sacramento", "newfoundland",
])
def test_generic_terms_are_blacklisted(term):
    assert term in cm.BLACKLISTED_TERMS


@pytest.mark.parametrize("sentence", [
    "The plant's technical team responded within minutes",
    "Security staff evacuated the building",
    "A solar farm was damaged in the blaze",
    "The contract was awarded last week",
    "Investigators travelled to Sacramento to review the findings",
])
def test_blacklisted_words_in_ordinary_prose_match_nothing(sentence):
    assert cm.find_client_match(sentence) is None


def test_blacklist_drops_the_bare_term_but_keeps_longer_names_containing_it():
    """'solar' is blacklisted as a standalone client name, but a real
    multi-word company that happens to contain the word must survive."""
    table = cm._build_search_table(["Solar", "Canadian Solar Industries"], [])
    canonicals = {canonical for _, canonical in table}

    assert "Solar" not in canonicals
    assert "Canadian Solar Industries" in canonicals


def test_real_clients_still_match():
    assert cm.find_client_match(
        "ExxonMobil fined 267,000 after five flammable hydrocarbon leaks at Fife plant"
    ) == "ExxonMobil"
    assert cm.find_client_match("Explosion at a Valero refinery in Texas") == "Valero"


def test_ambiguous_terms_are_documented_but_not_blacklisted():
    """Williams is a real pipeline operator AND a common surname. Blacklisting
    it would lose the true matches (it correctly tagged a Glenpool pipeline
    story), so it is recorded as ambiguous instead — a known gap, not a fix."""
    assert "williams" in cm.AMBIGUOUS_TERMS
    assert not (cm.AMBIGUOUS_TERMS & cm.BLACKLISTED_TERMS)


def test_build_client_list_skips_admin_folders():
    import build_client_list as bcl
    for name in ["Cancelled", "Proposals", "Technical", "Security"]:
        assert name.lower() in bcl.SKIP_EXACT
