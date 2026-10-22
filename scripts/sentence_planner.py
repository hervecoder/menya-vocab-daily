#!/usr/bin/env python3
"""Choose how many sentence pairs fit comfortably in each video.

The renderer supports content-sized sentence cards, so the answer is not a
fixed 4 pairs.  This planner measures every candidate 5- and 6-pair group with
the exact layout code used by ``generate.py`` and then picks a complete
partition of the source pairs.

Rules (deliberately layout-first, not fixed):

- Every video must fit between the section label and the promo banner.
- A card may wrap to at most two lines per language.
- A 6-pair video is preferred when its cards still average at least 27 px,
  keep at least a 24 px gap, and at most two cards need the extra line;
  otherwise the planner keeps 5 pairs so the text does not become cramped.
- A small score also prefers larger type and a larger card gap, then a
  dynamic-programming pass makes sure every source pair is covered exactly
  once and no partial video is created.

Usage:

    from sentence_planner import plan_sentence_groups
    groups = plan_sentence_groups(pairs)
"""

from __future__ import annotations

import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import generate  # noqa: E402

MIN_PAIRS = 5
MAX_PAIRS = 6

# A six-card video must stay readable; if it cannot, use five cards instead.
SIX_MIN_AVG_FONT = 27.0
SIX_MAX_WRAPPED = 2
SIX_MIN_GAP = 24

# A six-card group gets a large bonus, so it is used whenever it passes the
# readability gate.  Type size and breathing room break any remaining ties.
SIX_BONUS = 1000.0
AVG_FONT_WEIGHT = 4.0
GAP_WEIGHT = 0.5
WRAP_PENALTY = 15.0


@dataclass(frozen=True)
class SentenceGroup:
    """One planned sentence video; ``end`` is exclusive."""

    start: int
    end: int
    avg_font: float
    min_font: int
    max_font: int
    wrapped_cards: int
    gap: int
    used_height: int
    available_height: int
    score: float

    @property
    def pair_count(self) -> int:
        return self.end - self.start

    @property
    def line_count(self) -> int:
        return self.pair_count * 2


def _group_metrics(pairs, start: int, count: int, context):
    group = pairs[start : start + count]
    plan = generate.sentence_video_layout(group, context)
    layouts = plan["layouts"]
    sizes = [layout["size"] for layout in layouts]
    wrapped = sum(
        1
        for layout in layouts
        if len(layout["eng_lines"]) + len(layout["kin_lines"]) > 2
    )
    too_long = any(
        len(layout["eng_lines"]) > 2 or len(layout["kin_lines"]) > 2
        for layout in layouts
    )
    avg_font = statistics.mean(sizes)
    readable = plan["fits"] and not too_long
    if count == MAX_PAIRS:
        readable = readable and (
            avg_font >= SIX_MIN_AVG_FONT
            and wrapped <= SIX_MAX_WRAPPED
            and plan["gap"] >= SIX_MIN_GAP
        )
    score = (
        (SIX_BONUS if count == MAX_PAIRS else 0.0)
        + avg_font * AVG_FONT_WEIGHT
        + plan["gap"] * GAP_WEIGHT
        - wrapped * WRAP_PENALTY
    )
    return SentenceGroup(
        start=start,
        end=start + count,
        avg_font=avg_font,
        min_font=min(sizes),
        max_font=max(sizes),
        wrapped_cards=wrapped,
        gap=plan["gap"],
        used_height=plan["used_h"],
        available_height=plan["space"],
        score=score,
    ), readable


def plan_sentence_groups(pairs, context=None):
    """Return a complete 5/6-pair partition of ``pairs``.

    Raises ``ValueError`` when no complete partition is possible.  The caller
    must not publish a partial sentence video.
    """
    if len(pairs) < MIN_PAIRS:
        raise ValueError(
            f"Need at least {MIN_PAIRS} sentence pairs, got {len(pairs)}."
        )

    if context is None:
        context = generate.frame_context(
            generate.DEFAULT_NOTE, generate.DEFAULT_BRAND
        )

    total = len(pairs)
    negative = float("-inf")
    best = [negative] * (total + 1)
    choice: list[SentenceGroup | None] = [None] * (total + 1)
    best[total] = 0.0

    metrics: dict[tuple[int, int], tuple[SentenceGroup, bool]] = {}

    for start in range(total - 1, -1, -1):
        for count in (MIN_PAIRS, MAX_PAIRS):
            end = start + count
            if end > total:
                continue
            key = (start, count)
            if key not in metrics:
                metrics[key] = _group_metrics(pairs, start, count, context)
            group, readable = metrics[key]
            if not readable or best[end] == negative:
                continue
            score = group.score + best[end]
            if score > best[start]:
                best[start] = score
                choice[start] = group

    if best[0] == negative or choice[0] is None:
        raise ValueError(
            f"Cannot split {total} sentence pairs into complete "
            f"{MIN_PAIRS}- or {MAX_PAIRS}-pair videos."
        )

    groups: list[SentenceGroup] = []
    cursor = 0
    while cursor < total:
        group = choice[cursor]
        if group is None:
            raise RuntimeError("Sentence planner left an uncovered gap.")
        groups.append(group)
        cursor = group.end
    return groups


def summary(groups) -> str:
    """Human-readable count such as ``12 x 6``."""
    counts: dict[int, int] = {}
    for group in groups:
        counts[group.pair_count] = counts.get(group.pair_count, 0) + 1
    return ", ".join(f"{count} x {n}-pair" for n, count in sorted(counts.items()))
