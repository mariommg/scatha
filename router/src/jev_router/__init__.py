"""Route text items (bookmarks, tabs, notes) to destinations with Jev judgments.

The model answers narrow questions; this module owns the policy. Each item gets
one Noul per topic (an item can be about several) plus one Choice for its main
topic, all in a single request. Thresholds and destinations live in a taxonomy
file, so each consumer (Smaug, Tab Tamer) brings its own.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul

NONE = "none"


@dataclass(frozen=True)
class Topic:
    key: str
    question: str
    yes_means: str
    no_means: str
    destination: str | None = None
    label: str | None = None  # display name; defaults to the key


@dataclass(frozen=True)
class Taxonomy:
    topics: tuple[Topic, ...]
    route_at: float  # primary topic at or above this -> route
    review_at: float  # best topic at or above this (but below route_at) -> review
    model: str | None = None
    also_at: float | None = None  # other topics at or above this are listed on the note too; default: route_at

    @property
    def also(self) -> float:
        return self.route_at if self.also_at is None else self.also_at

    @classmethod
    def load(cls, path: str | Path) -> Taxonomy:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        topics = tuple(Topic(key=k, **v) for k, v in raw["topics"].items())
        if NONE in {t.key for t in topics}:
            raise ValueError(f"'{NONE}' is reserved")
        return cls(
            topics=topics,
            route_at=raw["thresholds"]["route"],
            review_at=raw["thresholds"]["review"],
            model=raw.get("model"),
            also_at=raw["thresholds"].get("also"),
        )

    def questions(self) -> dict:
        qs = {
            f"is_{t.key}": Noul(
                instructions=t.question, criteria={"true": t.yes_means, "false": t.no_means}
            )
            for t in self.topics
        }
        qs["primary"] = Choice(
            instructions="Which one of these topics is this item mainly about?",
            criteria={t.key: t.yes_means for t in self.topics}
            | {NONE: "None of the other topics describes this item."},
        )
        return qs


@dataclass
class Result:
    item_id: str
    status: str  # route | review | skip
    primary: str
    primary_confidence: float
    topics: dict[str, float]  # p(yes) per topic
    destination: str | None
    input_tokens: int | None = None
    error: str | None = None
    extra: dict = field(default_factory=dict)


def decide(tax: Taxonomy, topics: dict[str, float]) -> tuple[str, str]:
    """Pure policy: probabilities -> (status, topic used for placement).

    The strongest Noul places the item. It beat placing by the main-topic
    Choice 14/15 to 13/15 on 15 reviewed verdicts (2026-10-06); the Choice is still
    asked and logged as a second opinion.
    """
    best = max(topics, key=topics.get)
    if topics[best] >= tax.route_at:
        return "route", best
    if topics[best] >= tax.review_at:
        return "review", best
    return "skip", best


class Router:
    def __init__(self, taxonomy: Taxonomy, concurrency: int = 8):
        self.tax = taxonomy
        self.client = AsyncTypeSafeClient(model=taxonomy.model)
        self.sem = asyncio.Semaphore(concurrency)
        self.dest = {t.key: t.destination for t in taxonomy.topics}

    async def route(self, item_id: str, state: dict) -> Result:
        async with self.sem:
            try:
                resp = await self.client.system_one(state, self.tax.questions())
            except Exception as e:  # keep the batch going; surface per item
                return Result(item_id, "error", NONE, 0.0, {}, None, error=f"{type(e).__name__}: {e}")
        a = resp.answers
        topics = {t.key: float(a[f"is_{t.key}"].noul) for t in self.tax.topics}
        status, placed = decide(self.tax, topics)
        return Result(
            item_id=item_id,
            status=status,
            primary=placed,
            primary_confidence=float(a["primary"].confidence),
            topics=topics,
            destination=self.dest.get(placed) if status != "skip" else None,
            input_tokens=resp.usage.input_tokens,
            extra={"choice": a["primary"].choice},
        )

    async def route_many(self, items: list[tuple[str, dict]]) -> list[Result]:
        return await asyncio.gather(*(self.route(i, s) for i, s in items))
