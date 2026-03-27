"""Translate non-English article titles to English for clustering and display.

Uses deep-translator (Google Translate free tier) to translate titles.
Only translates titles that aren't already in English.
"""

import logging
import re
import time

from deep_translator import GoogleTranslator

logger = logging.getLogger(__name__)

# Language codes for regions we fetch from
_REGION_LANGUAGES = {
    "France": "fr",
    "Germany": "de",
    "Netherlands": "nl",
    "Italy": "it",
}

# Quick heuristic: if most words are ASCII and contain common English words, it's English
_ENGLISH_MARKERS = {
    "the", "a", "an", "in", "at", "on", "of", "to", "for", "and", "is",
    "was", "are", "has", "have", "by", "with", "from", "after", "fire",
    "explosion", "leak", "spill", "plant", "chemical", "refinery",
}


def _is_likely_english(text: str) -> bool:
    """Quick check if a title is likely already in English."""
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if not words:
        return False
    english_count = sum(1 for w in words if w in _ENGLISH_MARKERS)
    # If at least 20% of words are common English words, consider it English
    return english_count / len(words) >= 0.2


def translate_titles(events: list[dict], batch_size: int = 20) -> int:
    """Translate non-English event titles to English.

    Adds 'title_en' field to events that need translation.
    Skips events that already have 'title_en' or are already in English.

    Returns the number of titles translated.
    """
    to_translate = []
    for e in events:
        # Skip if already has English title
        if e.get("title_en"):
            continue
        title = e.get("title", "")
        if not title:
            continue
        # Skip if already English
        if _is_likely_english(title):
            e["title_en"] = title
            continue
        to_translate.append(e)

    if not to_translate:
        logger.info("No titles need translation")
        return 0

    logger.info("Translating %d non-English titles...", len(to_translate))
    translated_count = 0

    # Translate in batches to be respectful to the API
    for i in range(0, len(to_translate), batch_size):
        batch = to_translate[i:i + batch_size]
        titles = [e["title"] for e in batch]

        try:
            translator = GoogleTranslator(source="auto", target="en")
            results = translator.translate_batch(titles)

            for e, result in zip(batch, results):
                if result and result != e["title"]:
                    e["title_en"] = result
                    translated_count += 1
                else:
                    e["title_en"] = e["title"]

            # Small delay between batches to avoid rate limiting
            if i + batch_size < len(to_translate):
                time.sleep(0.5)

        except Exception as ex:
            logger.warning("Translation batch failed: %s — falling back to originals", ex)
            for e in batch:
                if not e.get("title_en"):
                    e["title_en"] = e["title"]

    logger.info("Translated %d titles to English", translated_count)
    return translated_count
