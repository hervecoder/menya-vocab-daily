"""Does the Buffer ledger already contain a post due on a given Kigali day?

The safety-net workflow runs 90 minutes after the primary dispatch. It uses
this to decide whether the primary run actually scheduled today's video:

  missing=true   -> no post is due today, re-dispatch the pipeline
  missing=false  -> today is covered, exit immediately

A post counts as covering the day when its `due_at` falls on that date in
Africa/Kigali and its status is not `failed`.

Usage:
    python scripts/check_today_post.py
    python scripts/check_today_post.py --date 2026-10-22
    python scripts/check_today_post.py --ledger data/buffer_queue.json \\
        --github-output "$GITHUB_OUTPUT"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KIGALI = timezone(timedelta(hours=2), "Africa/Kigali")


def posts_due_on(ledger: Path, day: date) -> list[dict]:
    """Return ledger posts whose due_at lands on `day` in Kigali time."""
    try:
        payload = json.loads(ledger.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []

    hits: list[dict] = []
    for post in payload.get("posts") or []:
        due_at = post.get("due_at")
        if not due_at:
            continue
        try:
            when = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when.astimezone(KIGALI).date() != day:
            continue
        if str(post.get("status", "")).lower() == "failed":
            continue
        hits.append(post)
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default="data/buffer_queue.json")
    parser.add_argument(
        "--date",
        default=None,
        help="YYYY-MM-DD to check (default: today in Africa/Kigali)",
    )
    parser.add_argument("--github-output", default=None)
    args = parser.parse_args(argv)

    day = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(KIGALI).date()
    )

    hits = posts_due_on(Path(args.ledger), day)
    missing = "false" if hits else "true"

    print(f"ledger: {args.ledger}")
    print(f"kigali day: {day}")
    print(f"posts due: {len(hits)}")
    for post in hits:
        print(
            "  {key}  status={status}  due={due}".format(
                key=post.get("key"), status=post.get("status"), due=post.get("due_at")
            )
        )
    print(f"missing={missing}")

    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"missing={missing}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
