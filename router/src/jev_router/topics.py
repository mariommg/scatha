"""Turn a user's topics ("AI: models, tools, agents...") into the taxonomy Jev reads.

Jev answers one yes/no question per topic, so each topic needs a question plus a
plain description of what counts as yes and what counts as no. A user writes one
line; Haiku can draft the rest with the other topics in view (so the definitions
contrast instead of overlapping), or a plain template fills in without a model call.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

MODEL = "jev-1.13.0"
THRESHOLDS = {"route": 0.3, "review": 0.3}

# Presets. The first five were tuned against real review verdicts (2026-10).
PRESETS = {
    "AI": {
        "question": "Is this item about artificial intelligence?",
        "yes_means": "About AI: language models, AI tools and products, agents, prompting, coding with AI "
                     "assistants, AI research, or AI's effects on work and society. Terms like skills, mods, MCP, "
                     "CLAUDE.md, subagents, Claude, Opus, or GPT refer to AI tooling.",
        "no_means": "AI is absent or only mentioned in passing, or AI was just the tool in a story about "
                    "something else (an AI helped find a math result).",
    },
    "Knowledge work": {
        "question": "Is this item about doing thinking work well?",
        "yes_means": "About how knowledge workers learn, research, write, take notes, make decisions, manage "
                     "attention, collaborate, or build careers in organizations where thinking work happens.",
        "no_means": "Not about how thinking work is done.",
    },
    "General knowledge": {
        "question": "Is this item useful or unusual knowledge about the world?",
        "yes_means": "Facts, ideas, or insights worth remembering: science, math, history, markets and investing, "
                     "how things work, or hard-won perspectives on life.",
        "no_means": "How-to tips, opinion, news chatter, self-promotion, jokes, or nothing worth remembering.",
    },
    "Life hacks": {
        "question": "Is this item thoughts, tools, or practical advice for living a better personal life?",
        "yes_means": "Thoughts, tools, and practical advice for a more focused, productive, satisfying personal "
                     "life: habits, health and energy, self-discipline, money, managing personal commitments and "
                     "relationships, finding personal meaning, or tricks with everyday tools and technology.",
        "no_means": "Facts, ideas, stories, or commentary with nothing to apply to one's own life. Advice about "
                    "careers, job searching, or how to think and work belongs to knowledge work.",
    },
    "Journalism": {
        "question": "Is this item in-depth journalism or reportage?",
        "yes_means": "Reported, in-depth work about real events or people: investigations, narrative features, "
                     "explanatory reporting, first-hand accounts, or the stories behind them.",
        "no_means": "Commentary, hot takes, tips, announcements, or short news blurbs.",
    },
    "Programming": {"description": "Writing software: languages, libraries, tools, debugging, architecture, and the craft of code."},
    "Science": {"description": "Research findings, discoveries, and explanations from the natural and social sciences."},
    "History": {"description": "Stories, sources, and analysis about the past."},
    "Investing": {"description": "Markets, investing strategies, companies, and personal finance decisions."},
    "Design": {"description": "Visual, product, and interaction design: examples, principles, and tools."},
    "Health": {"description": "Fitness, nutrition, sleep, and medical findings someone could act on."},
    "Cooking": {"description": "Recipes, techniques, and food knowledge."},
}


def slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return s or "topic"


def template_definition(label: str, description: str) -> dict:
    """No-model fallback: the user's own words become the yes side."""
    return {
        "question": f"Is this item about {label.lower()}?",
        "yes_means": description.strip().rstrip(".") + ".",
        "no_means": f"{label} is absent or only mentioned in passing.",
    }


DRAFT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["question", "yes_means", "no_means"],
    "properties": {
        "question": {"type": "string", "description": "One plain yes/no question, e.g. 'Is this item about X?'"},
        "yes_means": {"type": "string", "description": "What counts as yes: concrete kinds of posts, 1-3 sentences"},
        "no_means": {"type": "string", "description": "What counts as no, including near-misses that belong to the other topics"},
    },
}


def draft_definition(label: str, description: str, others: list[str]) -> dict:
    """Ask Haiku for a sharper definition. Falls back to the template on any failure."""
    from .notes import haiku
    prompt = (
        "A small classifier model will read your definition and answer yes or no for each saved X post. "
        "Write a definition for the topic below. Keep it to one narrow judgment, use concrete examples of "
        "posts that count, avoid double negatives, and in no_means name the nearby things that belong to "
        "the user's other topics instead.\n\n"
        + json.dumps({"topic": label, "user_description": description, "other_topics": others}, indent=1)
    )
    try:
        answer, _ = haiku([{"type": "text", "text": prompt}], DRAFT_SCHEMA)
        return {k: answer[k].strip() for k in ("question", "yes_means", "no_means")}
    except Exception:
        return template_definition(label, description)


def taxonomy(topics: list[dict]) -> dict:
    """topics: [{label, question, yes_means, no_means, destination}] -> taxonomy document."""
    out, used = {}, set()
    for t in topics:
        key = slug(t["label"])
        while key in used or key == "none":
            key += "_2"
        used.add(key)
        out[key] = {k: t[k] for k in ("label", "question", "yes_means", "no_means", "destination")}
    return {"model": MODEL, "thresholds": dict(THRESHOLDS), "topics": out}


def write_taxonomy(topics: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ("# Your topics. Jev reads question / yes_means / no_means for every bookmark; edit them in plain\n"
              "# words and the next run uses them. A bookmark goes to the topic it scores highest on, if that\n"
              "# score is at least thresholds.route; below that it's skipped.\n")
    path.write_text(header + yaml.safe_dump(taxonomy(topics), sort_keys=False, allow_unicode=True, width=100),
                    encoding="utf-8")
    return path
