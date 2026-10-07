"""Invariants of the routing policy. No API calls."""

import itertools
from pathlib import Path

from jev_router import Taxonomy, Topic, decide

# Four topics with route 0.8 / review 0.3, so every status (route, review, skip) is reachable.
TAX = Taxonomy(
    topics=tuple(Topic(key=k, question=f"Is this about {k}?", yes_means=k, no_means=f"not {k}")
                 for k in ("ai", "knowledge_work", "life_hacks", "long_form")),
    route_at=0.8, review_at=0.3)
KEYS = [t.key for t in TAX.topics]
RANK = {"skip": 0, "review": 1, "route": 2}
GRID = [0.05, 0.3, 0.5, 0.79, 0.8, 0.95]


def grid():
    for ps in itertools.product(GRID, repeat=len(KEYS)):
        yield dict(zip(KEYS, ps))


def test_more_evidence_never_demotes():
    # Raising any one topic's probability never moves an item toward skip.
    for topics in grid():
        before, _ = decide(TAX, topics)
        for k in KEYS:
            bumped = topics | {k: min(1.0, topics[k] + 0.2)}
            after, _ = decide(TAX, bumped)
            assert RANK[after] >= RANK[before], (topics, k)


def test_placement_is_a_supported_topic():
    # Wherever an item is placed, its Noul clears the review bar.
    for topics in grid():
        status, placed = decide(TAX, topics)
        if status != "skip":
            assert topics[placed] >= TAX.review_at


def test_any_topic_over_the_bar_routes():
    for topics in grid():
        status, _ = decide(TAX, topics)
        assert (status == "route") == (max(topics.values()) >= TAX.route_at)


def test_placed_in_strongest_topic():
    for topics in grid():
        _, placed = decide(TAX, topics)
        assert topics[placed] == max(topics.values())
