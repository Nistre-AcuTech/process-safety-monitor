"""Cluster related news articles about the same incident.

Uses title word overlap (Jaccard similarity) within a time window
to group articles from different outlets covering the same event.
"""

import re
from datetime import datetime

_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "can", "do", "for", "from", "had", "has", "have", "he", "her",
    "here", "how", "if", "in", "is", "it", "its", "may", "more",
    "new", "no", "not", "of", "on", "or", "our", "out", "re", "s",
    "say", "says", "she", "so", "t", "than", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "to", "up",
    "us", "ve", "was", "we", "were", "what", "when", "which", "who",
    "why", "will", "with", "you", "your",
    # News filler
    "after", "about", "also", "amid", "before", "being", "could",
    "did", "does", "during", "get", "got", "into", "just", "know",
    "like", "make", "many", "most", "much", "near", "now", "off",
    "over", "some", "still", "such", "take", "told", "two", "under",
    "very", "would",
})

# Pattern to strip source suffix like " - AP News" or " | CNN"
_SOURCE_SUFFIX = re.compile(r"\s*[-|–—]\s*[A-Z][\w\s.,'&]+$")


def _significant_words(title: str) -> set[str]:
    """Extract significant words from a title."""
    title = _SOURCE_SUFFIX.sub("", title)
    words = re.findall(r"[a-z]+", title.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 2}


def _title_similarity(title_a: str, title_b: str) -> float:
    """Jaccard similarity of significant words between two titles."""
    words_a = _significant_words(title_a)
    words_b = _significant_words(title_b)
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def cluster_events(
    events: list[dict],
    similarity_threshold: float = 0.45,
    time_window_hours: int = 72,
) -> list[dict]:
    """Assign cluster_id to each event using union-find on title similarity.

    Modifies events in-place and returns the same list.
    """
    n = len(events)
    if n == 0:
        return events

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # Pre-parse dates and significant words for speed
    dates: list[datetime | None] = []
    words: list[set[str]] = []
    for e in events:
        d = e.get("date")
        try:
            dates.append(datetime.fromisoformat(d) if d else None)
        except (ValueError, TypeError):
            dates.append(None)
        words.append(_significant_words(e.get("title", "")))

    max_seconds = time_window_hours * 3600

    for i in range(n):
        if not words[i]:
            continue
        for j in range(i + 1, n):
            if not words[j]:
                continue

            # Time window check (cheap)
            if dates[i] and dates[j]:
                if abs((dates[i] - dates[j]).total_seconds()) > max_seconds:
                    continue

            # Jaccard similarity using pre-computed word sets
            intersection = words[i] & words[j]
            union_set = words[i] | words[j]
            if union_set and len(intersection) / len(union_set) >= similarity_threshold:
                union(i, j)

    # Assign cluster IDs
    cluster_map: dict[int, int] = {}
    cluster_counter = 0
    for i in range(n):
        root = find(i)
        if root not in cluster_map:
            cluster_map[root] = cluster_counter
            cluster_counter += 1
        events[i]["cluster_id"] = cluster_map[root]

    return events
