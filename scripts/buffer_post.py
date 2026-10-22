#!/usr/bin/env python3
"""Schedule the generated video on the perime.rw250 TikTok channel in Buffer.

The workflow renders one sentence video, commits it so the mp4 has a public
raw.githubusercontent.com URL, then calls this script.  The post is created as
a custom-scheduled TikTok post with *no caption* at the requested Kigali time.

Buffer's public API can create posts but does not expose an endpoint for
editing a channel's reusable queue schedule.  Scheduling the post explicitly
with ``mode: customScheduled`` is therefore how the daily slot is enforced.

Environment:
    BUFFER_API_KEY   Buffer API key (GitHub Actions secret)

Typical:
    python scripts/buffer_post.py \
        --content-id sentence_0002 \
        --cycle 1 \
        --video-url https://raw.githubusercontent.com/.../output/sentence_0002.mp4 \
        --post-time 18:30 \
        --ledger data/buffer_queue.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "data" / "buffer_queue.json"
API_URL = "https://api.buffer.com"
USER_AGENT = "menya-vocab-video/1.0"
RETRYABLE_HTTP = {429, 500, 502, 503, 504}
RETRYABLE_NETWORK = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
)


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def write_github_output(path: str | None, values: dict[str, Any]) -> None:
    if not path:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = str(value)
            if "\n" in text:
                text = text.replace("\n", "%0A")
            handle.write(f"{key}={text}\n")


def graphql(
    token: str,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    attempts: int = 3,
) -> dict[str, Any]:
    """POST one GraphQL request, retrying transient network/5xx failures."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    last_error = "unknown error"
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "Connection": "close",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            if data.get("errors"):
                messages = "; ".join(
                    str(item.get("message", "GraphQL error"))
                    for item in data["errors"]
                )
                raise SystemExit(f"Buffer API error: {messages}")
            return data
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            last_error = f"HTTP {exc.code}: {detail}"
            if exc.code in RETRYABLE_HTTP and attempt < attempts:
                time.sleep(4 * attempt)
                continue
            raise SystemExit(f"Buffer API {last_error}") from exc
        except RETRYABLE_NETWORK as exc:
            last_error = str(exc)
            if attempt < attempts:
                time.sleep(4 * attempt)
                continue
            raise SystemExit(f"Buffer API network error: {last_error}") from exc
        except json.JSONDecodeError as exc:
            raise SystemExit("Buffer API returned invalid JSON") from exc
    raise SystemExit(f"Buffer API failed: {last_error}")


def fetch_organizations(token: str) -> list[dict[str, Any]]:
    data = graphql(
        token,
        """
        query {
          account {
            organizations { id name }
          }
        }
        """,
    )
    try:
        return data["data"]["account"]["organizations"]
    except (KeyError, TypeError) as exc:
        raise SystemExit("Buffer API did not return account organizations") from exc


def fetch_channels(token: str, organization_id: str) -> list[dict[str, Any]]:
    data = graphql(
        token,
        """
        query($organizationId: OrganizationId!) {
          channels(input: { organizationId: $organizationId }) {
            id
            name
            displayName
            service
            type
            descriptor
            externalLink
            isDisconnected
            isLocked
            isQueuePaused
            timezone
          }
        }
        """,
        {"organizationId": organization_id},
    )
    try:
        return data["data"]["channels"]
    except (KeyError, TypeError) as exc:
        raise SystemExit("Buffer API did not return channels") from exc


def zone_for(name: str):
    """Return a tzinfo, with a fixed-offset fallback for Windows test runs."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception as exc:  # ZoneInfoNotFoundError or missing tzdata
        if name == "Africa/Kigali":
            return timezone(timedelta(hours=2), name)
        raise SystemExit(
            f"Unknown timezone {name!r}; install tzdata or pick a supported zone"
        ) from exc


def parse_post_time(raw: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = raw.strip().split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise SystemExit("--post-time must be HH:MM (for example 18:30)") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SystemExit("--post-time must be a valid 24-hour time")
    return hour, minute


def next_due_at(raw_time: str, tz_name: str, lead_minutes: int) -> datetime:
    hour, minute = parse_post_time(raw_time)
    tz = zone_for(tz_name)
    current = datetime.now(timezone.utc).astimezone(tz)
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= current + timedelta(minutes=lead_minutes):
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def check_video_url(url: str, attempts: int, delay: int) -> None:
    """Make sure Buffer will be able to fetch the committed mp4."""
    last_error = "not checked"
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Connection": "close",
            },
            method="HEAD",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                length = response.headers.get("Content-Length")
                if response.status != 200:
                    last_error = f"HTTP {response.status}"
                elif length and int(length) < 1024:
                    last_error = f"file is only {length} bytes"
                else:
                    print(f"video URL OK: {url} ({length or 'unknown'} bytes)")
                    return
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            # Some hosts reject HEAD; a one-byte ranged GET is a safe fallback.
            if exc.code in {403, 405}:
                try:
                    fallback = urllib.request.Request(
                        url,
                        headers={
                            "User-Agent": USER_AGENT,
                            "Range": "bytes=0-0",
                            "Connection": "close",
                        },
                    )
                    with urllib.request.urlopen(fallback, timeout=30) as response:
                        if response.status in {200, 206}:
                            print(f"video URL OK: {url}")
                            return
                except Exception as fallback_exc:  # noqa: BLE001
                    last_error = str(fallback_exc)
        except RETRYABLE_NETWORK as exc:
            last_error = str(exc)
        if attempt < attempts:
            print(
                f"video URL not ready ({last_error}); retry "
                f"{attempt}/{attempts} in {delay}s"
            )
            time.sleep(delay)
    raise SystemExit(f"Video URL is not publicly readable: {url} ({last_error})")


def find_channel(
    channels: list[dict[str, Any]], channel_id: str, channel_name: str
) -> dict[str, Any]:
    candidates = channels
    if channel_id:
        candidates = [item for item in candidates if item.get("id") == channel_id]
    else:
        candidates = [
            item for item in candidates if item.get("service") == "tiktok"
        ]
        if channel_name:
            candidates = [
                item
                for item in candidates
                if item.get("name") == channel_name
                or item.get("displayName") == channel_name
            ]
    if not candidates:
        raise SystemExit(
            "No matching TikTok channel found in Buffer. "
            "Use --channel-id or connect the account first."
        )
    if len(candidates) > 1:
        names = ", ".join(
            f"{item.get('name')} ({item.get('id')})" for item in candidates
        )
        raise SystemExit(f"Multiple TikTok channels found; choose one: {names}")
    return candidates[0]


def resolve_organization(
    token: str, organization_id: str, organization_name: str
) -> dict[str, Any]:
    organizations = fetch_organizations(token)
    if not organizations:
        raise SystemExit("Buffer account has no organizations")
    if organization_id:
        for item in organizations:
            if item.get("id") == organization_id:
                return item
        raise SystemExit(f"Buffer organization not found: {organization_id}")
    if organization_name:
        matches = [
            item for item in organizations if item.get("name") == organization_name
        ]
        if len(matches) == 1:
            return matches[0]
        raise SystemExit(f"Buffer organization not found: {organization_name}")
    if len(organizations) == 1:
        return organizations[0]
    names = ", ".join(
        f"{item.get('name')} ({item.get('id')})" for item in organizations
    )
    raise SystemExit(f"Multiple Buffer organizations found; choose one: {names}")


def find_post_by_asset(
    token: str, organization_id: str, channel_id: str, video_url: str
) -> dict[str, Any] | None:
    """Look for a still-pending Buffer post that already uses this mp4.

    The ledger in the repository is the first guard against duplicates. This
    second guard covers the window where Buffer accepted the post but the run
    died before the ledger was committed: the mp4 file name is unique per
    sentence video, so an existing scheduled post carrying the same file means
    the video is already queued and must not be scheduled twice.

    Matching is done on the file name rather than the whole URL on purpose:
    the same video can be served from the upstream repo or from a fork, and
    both hosts must be recognised as "already posted".
    """
    target = video_url.rsplit("/", 1)[-1]
    if not target:
        return None
    data = graphql(
        token,
        """
        query($organizationId: OrganizationId!, $channelId: ChannelId!) {
          posts(
            input: {
              organizationId: $organizationId
              filter: { channelIds: [$channelId] }
            }
            first: 50
          ) {
            edges {
              node {
                id
                status
                dueAt
                shareMode
                text
                channelId
                assets { type source }
              }
            }
          }
        }
        """,
        {"organizationId": organization_id, "channelId": channel_id},
    )
    edges = data.get("data", {}).get("posts", {}).get("edges") or []
    for edge in edges:
        node = edge.get("node") or {}
        if node.get("status") not in {"scheduled", "pending"}:
            continue
        for asset in node.get("assets") or []:
            if asset.get("type") != "video":
                continue
            source = asset.get("source") or ""
            if source.rsplit("/", 1)[-1] == target:
                return node
    return None


def create_post(
    token: str, channel_id: str, video_url: str, due_at: str, source: str
) -> dict[str, Any]:
    mutation = """
    mutation CreateTikTokPost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess {
          post {
            id
            channelId
            text
            status
            dueAt
            shareMode
            isCustomScheduled
          }
        }
        ... on MutationError {
          message
        }
      }
    }
    """
    variables = {
        "input": {
            "text": "",
            "channelId": channel_id,
            "schedulingType": "automatic",
            "mode": "customScheduled",
            "dueAt": due_at,
            "source": source,
            "assets": [
                {
                    "video": {
                        "url": video_url,
                        "metadata": {"thumbnailOffset": 1000},
                    }
                }
            ],
        }
    }
    data = graphql(token, mutation, variables)
    result = data.get("data", {}).get("createPost")
    if not result:
        raise SystemExit("Buffer API returned no createPost result")
    if result.get("message"):
        raise SystemExit(f"Buffer could not create the post: {result['message']}")
    post = result.get("post")
    if not post:
        raise SystemExit("Buffer API returned an empty post")
    return post


def ledger_key(content_id: str, cycle: int) -> str:
    return f"{content_id}@cycle-{cycle}"


def update_ledger(
    path: Path,
    *,
    content_id: str,
    cycle: int,
    post: dict[str, Any],
    video_url: str,
) -> None:
    if path.exists():
        payload = read_json(path)
    else:
        payload = {"posts": []}
    posts = payload.setdefault("posts", [])
    key = ledger_key(content_id, cycle)
    entry = {
        "key": key,
        "content_id": content_id,
        "cycle": cycle,
        "buffer_post_id": post.get("id"),
        "channel_id": post.get("channelId"),
        "status": post.get("status"),
        "due_at": post.get("dueAt"),
        "share_mode": post.get("shareMode"),
        "is_custom_scheduled": post.get("isCustomScheduled"),
        "text": post.get("text", ""),
        "video_url": video_url,
        "scheduled_at": now_iso(),
    }
    for index, existing in enumerate(posts):
        if existing.get("key") == key:
            posts[index] = entry
            break
    else:
        posts.append(entry)
    payload["updated_at"] = now_iso()
    write_json(path, payload)


def already_scheduled(path: Path, content_id: str, cycle: int) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = read_json(path)
    key = ledger_key(content_id, cycle)
    for item in payload.get("posts", []):
        if item.get("key") == key:
            return item
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Schedule one blank-caption TikTok video in Buffer."
    )
    parser.add_argument("--content-id", required=True, help="Rotation id, e.g. sentence_0002")
    parser.add_argument("--cycle", type=int, default=1, help="Sentence rotation cycle")
    parser.add_argument("--video-url", required=True, help="Public mp4 URL for Buffer to fetch")
    parser.add_argument("--post-time", default=os.environ.get("BUFFER_POST_TIME", "18:30"))
    parser.add_argument("--timezone", default="", help="Override channel timezone")
    parser.add_argument("--lead-minutes", type=int, default=10)
    parser.add_argument("--organization-id", default=os.environ.get("BUFFER_ORGANIZATION_ID", ""))
    parser.add_argument("--organization-name", default="")
    parser.add_argument("--channel-id", default=os.environ.get("BUFFER_CHANNEL_ID", ""))
    parser.add_argument("--channel-name", default="perime.rw250")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--github-output", default="")
    parser.add_argument("--check-url-attempts", type=int, default=6)
    parser.add_argument("--check-url-delay", type=int, default=10)
    parser.add_argument("--skip-url-check", action="store_true")
    parser.add_argument("--force", action="store_true", help="Schedule even if the ledger has the key")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.cycle < 1:
        raise SystemExit("--cycle must be at least 1")

    existing = already_scheduled(args.ledger, args.content_id, args.cycle)
    if existing and not args.force:
        print(
            "already in Buffer ledger: "
            f"{args.content_id} cycle {args.cycle} -> {existing.get('buffer_post_id')}"
        )
        write_github_output(
            args.github_output,
            {
                "buffer_skipped": "true",
                "buffer_post_id": existing.get("buffer_post_id", ""),
                "buffer_due_at": existing.get("due_at", ""),
                "buffer_status": existing.get("status", ""),
            },
        )
        return

    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        raise SystemExit(
            "BUFFER_API_KEY is not set. Add the Buffer API key as a GitHub "
            "Actions repository secret (or export it locally)."
        )

    organization = resolve_organization(
        token, args.organization_id, args.organization_name
    )
    channels = fetch_channels(token, organization["id"])
    channel = find_channel(channels, args.channel_id, args.channel_name)
    if channel.get("isDisconnected"):
        raise SystemExit(f"Buffer channel is disconnected: {channel.get('name')}")
    if channel.get("isLocked"):
        raise SystemExit(f"Buffer channel is locked: {channel.get('name')}")
    if channel.get("isQueuePaused"):
        raise SystemExit(
            f"Buffer queue is paused for {channel.get('name')}; resume it before "
            "scheduling so the post is not silently held."
        )

    timezone_name = args.timezone or channel.get("timezone") or "Africa/Kigali"
    due_at = next_due_at(args.post_time, timezone_name, args.lead_minutes)
    due_at_text = iso_z(due_at)

    print(
        f"channel: {channel.get('name')} ({channel.get('descriptor')}, "
        f"{channel.get('id')})"
    )
    print(f"caption: <blank>")
    print(f"video:   {args.video_url}")
    print(f"schedule:{due_at_text} ({args.post_time} {timezone_name})")

    if args.dry_run:
        print("dry run: no Buffer post created")
        return

    if not args.skip_url_check:
        check_video_url(
            args.video_url,
            args.check_url_attempts,
            args.check_url_delay,
        )

    # Second duplicate guard: Buffer may already hold a pending post for this
    # exact mp4 even when the repository ledger was never committed.
    existing_post = find_post_by_asset(
        token,
        organization["id"],
        channel["id"],
        args.video_url,
    )
    if existing_post:
        print(
            "buffer:  already scheduled (post "
            f"{existing_post.get('id')}, due {existing_post.get('dueAt')}); "
            "no duplicate created"
        )
        update_ledger(
            args.ledger,
            content_id=args.content_id,
            cycle=args.cycle,
            post=existing_post,
            video_url=args.video_url,
        )
        write_github_output(
            args.github_output,
            {
                "buffer_skipped": "true",
                "buffer_post_id": existing_post.get("id", ""),
                "buffer_due_at": existing_post.get("dueAt", ""),
                "buffer_status": existing_post.get("status", ""),
            },
        )
        print(f"ledger:  {args.ledger}")
        return

    post = create_post(
        token,
        channel["id"],
        args.video_url,
        due_at_text,
        "menya-vocab-video",
    )
    update_ledger(
        args.ledger,
        content_id=args.content_id,
        cycle=args.cycle,
        post=post,
        video_url=args.video_url,
    )
    write_github_output(
        args.github_output,
        {
            "buffer_skipped": "false",
            "buffer_post_id": post.get("id", ""),
            "buffer_due_at": post.get("dueAt", ""),
            "buffer_status": post.get("status", ""),
        },
    )
    print(
        f"buffer:  post {post.get('id')} | status={post.get('status')} | "
        f"due={post.get('dueAt')} | shareMode={post.get('shareMode')}"
    )
    print(f"ledger:  {args.ledger}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
