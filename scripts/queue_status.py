"""How many videos does the Buffer queue still need?

    depth = non-failed posts whose due_at is still in the future
    add   = max(0, target - depth)

The workflow calls this with --target 5, which makes it a top-up worker: the
channel always has five posts sitting in Buffer, so two or three failed days
do not interrupt publishing. A single successful run refills the whole
deficit, because the daily job renders `add` videos in one pass.

Usage:
    python scripts/queue_status.py
    python scripts/queue_status.py --target 5 --github-output "$GITHUB_OUTPUT"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KIGALI = timezone(timedelta(hours=2), "Africa/Kigali")
DEFAULT_LEDGER = Path("data/buffer_queue.json")


def future_posts(ledger: Path, now: datetime) -> list[dict]:
    """Posts that are still pending in Buffer, sorted by due time."""
    try:
        payload = json.loads(ledger.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []

    posts: list[tuple[datetime, dict]] = []
    for post in payload.get("posts") or []:
        if str(post.get("status", "")).lower() == "failed":
            continue
        due_at = post.get("due_at")
        if not due_at:
            continue
        try:
            when = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when > now:
            posts.append((when, post))

    posts.sort(key=lambda item: item[0])
    return [post for _, post in posts]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--target", type=int, default=5)
    parser.add_argument("--github-output", default=None)
    args = parser.parse_args(argv)

    if args.target < 0:
        raise SystemExit("--target must be zero or more")

    now = datetime.now(timezone.utc)
    pending = future_posts(args.ledger, now)
    depth = len(pending)
    add = max(0, args.target - depth)

    print(f"ledger: {args.ledger}")
    print(f"now:    {now.isoformat(timespec='seconds')}")
    print(f"target: {args.target}   depth: {depth}   add: {add}")
    for post in pending:
        when = datetime.fromisoformat(str(post["due_at"]).replace("Z", "+00:00"))
        print(
            "  {due}  {key}  {status}".format(
                due=when.astimezone(KIGALI).strftime("%Y-%m-%d %H:%M %Z"),
                key=post.get("key"),
                status=post.get("status"),
            )
        )

    soonest = pending[0].get("due_at", "") if pending else ""
    latest = pending[-1].get("due_at", "") if pending else ""

    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"depth={depth}\n")
            handle.write(f"add={add}\n")
            handle.write(f"target={args.target}\n")
            handle.write(f"soonest={soonest}\n")
            handle.write(f"latest={latest}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
