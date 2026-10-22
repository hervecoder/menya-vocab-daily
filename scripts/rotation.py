#!/usr/bin/env python3
"""Pick the next vocabulary video from the chunk manifest and advance the cycle.

The rotation state lives in ``data/rotation_state.json`` and is committed by the
GitHub Actions workflow after a video is generated successfully. The manifest
(``data/rotation_manifest.json``) references complete video jobs inside the
chunk files under ``data/chunks/``.

Typical use:

    python scripts/rotation.py next \
        --out data/.current_job.json \
        --pending data/.rotation_pending.json

    python generate.py --batch-json data/.current_job.json --video

    python scripts/rotation.py advance \
        --pending data/.rotation_pending.json

``next`` only peeks. State is advanced by ``advance`` after the render succeeds,
so a failed render never skips a content item. When the last content is done,
the next content is ``content_0001`` of the next cycle.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data" / "rotation_manifest.json"
DEFAULT_STATE = ROOT / "data" / "rotation_state.json"
DEFAULT_OUT = ROOT / "data" / ".current_job.json"
DEFAULT_PENDING = ROOT / "data" / ".rotation_pending.json"


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def read_json(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
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


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = read_json(path)
    if not manifest.get("jobs"):
        raise SystemExit(f"Manifest has no jobs: {path}")
    if not manifest.get("chunks"):
        raise SystemExit(f"Manifest has no chunks: {path}")
    return manifest


def load_state(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    state = read_json(path)
    total_jobs = len(manifest["jobs"])
    if int(state.get("total_jobs", -1)) != total_jobs:
        raise SystemExit(
            "Rotation state is out of date: "
            f"state has total_jobs={state.get('total_jobs')}, "
            f"manifest has {total_jobs}. Rebuild the chunks or reset the state."
        )
    index = int(state.get("next_job_index", -1))
    if index < 0 or index >= total_jobs:
        raise SystemExit(
            f"State next_job_index={index} is outside 0..{total_jobs - 1}."
        )
    expected = manifest["jobs"][index]["id"]
    if state.get("next_content_id") not in (None, expected):
        raise SystemExit(
            "State next_content_id does not match the manifest: "
            f"{state.get('next_content_id')!r} vs {expected!r}."
        )
    return state


def resolve_job(
    manifest_path: Path,
    manifest: dict[str, Any],
    manifest_job: dict[str, Any],
) -> dict[str, Any]:
    chunk_id = int(manifest_job["chunk_id"])
    chunk_meta = next(
        (chunk for chunk in manifest["chunks"] if int(chunk["chunk_id"]) == chunk_id),
        None,
    )
    if chunk_meta is None:
        raise SystemExit(f"Chunk {chunk_id} is not listed in the manifest.")

    chunk_path = manifest_path.parent / str(chunk_meta["file"])
    chunk = read_json(chunk_path)
    jobs = chunk.get("jobs") or []
    job_index = int(manifest_job["job_index"])
    if job_index < 0 or job_index >= len(jobs):
        raise SystemExit(
            f"Job index {job_index} is outside chunk {chunk_id} "
            f"(0..{len(jobs) - 1})."
        )
    job = jobs[job_index]
    if job.get("id") != manifest_job.get("id"):
        raise SystemExit(
            f"Chunk job id mismatch: {job.get('id')!r} vs "
            f"{manifest_job.get('id')!r}."
        )
    if not job.get("pairs"):
        raise SystemExit(f"Content {job.get('id')!r} has no pairs.")
    if int(job.get("pair_count", len(job["pairs"]))) != len(job["pairs"]):
        raise SystemExit(f"Content {job.get('id')!r} has an inconsistent pair_count.")
    return job


def job_summary(
    manifest: dict[str, Any],
    manifest_job: dict[str, Any],
    state: dict[str, Any],
    job: dict[str, Any],
    auto: bool,
) -> dict[str, Any]:
    total_jobs = len(manifest["jobs"])
    if auto:
        index = int(manifest_job["content_number"]) - 1
        next_index = (index + 1) % total_jobs
        next_content_id = manifest["jobs"][next_index]["id"]
        next_cycle = int(state["cycle"]) + (1 if next_index == 0 else 0)
    else:
        next_content_id = state["next_content_id"]
        next_cycle = state["cycle"]
    return {
        "content_id": job["id"],
        "content_number": manifest_job["content_number"],
        "mode": job["mode"],
        "pair_count": job["pair_count"],
        "entry_start": manifest_job["entry_start"],
        "entry_end": manifest_job["entry_end"],
        "total_entries": state["total_entries"],
        "input_entries": state.get("input_entries", state["total_entries"]),
        "remainder_entries": state.get("remainder_entries", 0),
        "excluded_unverified_entries": state.get(
            "excluded_unverified_entries", 0
        ),
        "total_jobs": total_jobs,
        "cycle": state["cycle"],
        "next_content_id": next_content_id,
        "next_cycle": next_cycle,
        "auto": "true" if auto else "false",
    }


def write_batch(path: Path, job: dict[str, Any]) -> None:
    write_json(path, {"videos": [job]})


def cmd_next(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    state = load_state(args.state, manifest)
    index = int(state["next_job_index"])
    manifest_job = manifest["jobs"][index]
    job = resolve_job(args.manifest, manifest, manifest_job)

    write_batch(args.out, job)
    pending = {
        "cycle": state["cycle"],
        "next_job_index": index,
        "content_id": job["id"],
        "mode": job["mode"],
        "pair_count": job["pair_count"],
        "auto": True,
        "created_at": now_iso(),
    }
    write_json(args.pending, pending)

    outputs = job_summary(manifest, manifest_job, state, job, auto=True)
    write_github_output(args.github_output, outputs)
    print(
        f"next: {job['id']} ({job['mode']}, {job['pair_count']} pairs, "
        f"entries {manifest_job['entry_start']}-{manifest_job['entry_end']}, "
        f"cycle {state['cycle']}, {index + 1}/{len(manifest['jobs'])})"
    )


def cmd_advance(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    state = load_state(args.state, manifest)
    pending = read_json(args.pending)

    if pending.get("manual"):
        args.pending.unlink(missing_ok=True)
        print("manual selection: rotation state unchanged")
        return

    if int(pending.get("cycle", -1)) != int(state["cycle"]):
        raise SystemExit(
            "Pending job cycle does not match state; refusing to advance."
        )
    if int(pending.get("next_job_index", -1)) != int(state["next_job_index"]):
        raise SystemExit(
            "Pending job index does not match state; refusing to advance."
        )
    if pending.get("content_id") != state.get("next_content_id"):
        raise SystemExit(
            "Pending content does not match state; refusing to advance."
        )

    total_jobs = len(manifest["jobs"])
    index = int(state["next_job_index"])
    next_index = (index + 1) % total_jobs
    wrapped = next_index == 0

    state["last_content_id"] = pending["content_id"]
    state["next_job_index"] = next_index
    state["next_content_id"] = manifest["jobs"][next_index]["id"]
    state["updated_at"] = now_iso()
    if wrapped:
        state["cycle"] = int(state["cycle"]) + 1
        state["jobs_done_in_cycle"] = 0
    else:
        state["jobs_done_in_cycle"] = int(state.get("jobs_done_in_cycle", 0)) + 1

    write_json(args.state, state)
    args.pending.unlink(missing_ok=True)

    if wrapped:
        print(
            f"advanced: {pending['content_id']} -> {state['next_content_id']} "
            f"| cycle {state['cycle']} starts"
        )
    else:
        print(
            f"advanced: {pending['content_id']} -> {state['next_content_id']} "
            f"| cycle {state['cycle']}, "
            f"{state['jobs_done_in_cycle']}/{total_jobs} done"
        )


def cmd_select(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    state = load_state(args.state, manifest)
    manifest_job = next(
        (job for job in manifest["jobs"] if job["id"] == args.content_id),
        None,
    )
    if manifest_job is None:
        first = manifest["jobs"][0]["id"]
        last = manifest["jobs"][-1]["id"]
        raise SystemExit(
            f"Unknown content {args.content_id!r}; available range {first}..{last}."
        )

    job = resolve_job(args.manifest, manifest, manifest_job)
    write_batch(args.out, job)
    pending = {
        "cycle": state["cycle"],
        "next_job_index": state["next_job_index"],
        "content_id": job["id"],
        "mode": job["mode"],
        "pair_count": job["pair_count"],
        "manual": True,
        "created_at": now_iso(),
    }
    write_json(args.pending, pending)

    outputs = job_summary(manifest, manifest_job, state, job, auto=False)
    write_github_output(args.github_output, outputs)
    print(
        f"selected: {job['id']} ({job['mode']}, {job['pair_count']} pairs, "
        f"entries {manifest_job['entry_start']}-{manifest_job['entry_end']}); "
        "state will not advance"
    )


def cmd_status(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    state = load_state(args.state, manifest)
    index = int(state["next_job_index"])
    manifest_job = manifest["jobs"][index]
    print(
        json.dumps(
            {
                "state": state,
                "next_job": manifest_job,
                "manifest_meta": manifest.get("meta", {}),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_reset(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    meta = manifest.get("meta", {})
    jobs = manifest["jobs"]
    state = {
        "cycle": 1,
        "next_job_index": 0,
        "jobs_done_in_cycle": 0,
        "total_jobs": len(jobs),
        "total_entries": meta.get("video_entries", 0),
        "input_entries": meta.get("input_entries", 0),
        "remainder_entries": meta.get("remainder_entries", 0),
        "excluded_unverified_entries": meta.get(
            "excluded_unverified_entries", 0
        ),
        "last_content_id": None,
        "next_content_id": jobs[0]["id"],
        "updated_at": now_iso(),
    }
    write_json(args.state, state)
    print(f"reset: next {state['next_content_id']}, cycle 1, {len(jobs)} jobs")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    common.add_argument("--state", type=Path, default=DEFAULT_STATE)
    common.add_argument("--out", type=Path, default=DEFAULT_OUT)
    common.add_argument("--pending", type=Path, default=DEFAULT_PENDING)
    common.add_argument(
        "--github-output",
        default="",
        help="Optional path to $GITHUB_OUTPUT for workflow outputs.",
    )

    parser = argparse.ArgumentParser(
        description="Rotate through vocabulary video chunks and remember progress."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    next_parser = subparsers.add_parser(
        "next", parents=[common], help="Peek at the next content."
    )
    next_parser.set_defaults(func=cmd_next)

    advance_parser = subparsers.add_parser(
        "advance", parents=[common], help="Advance state after a successful render."
    )
    advance_parser.set_defaults(func=cmd_advance)

    select_parser = subparsers.add_parser(
        "select",
        parents=[common],
        help="Render a specific content without advancing state.",
    )
    select_parser.add_argument("--content-id", required=True)
    select_parser.set_defaults(func=cmd_select)

    status_parser = subparsers.add_parser(
        "status", parents=[common], help="Print state and next job."
    )
    status_parser.set_defaults(func=cmd_status)

    reset_parser = subparsers.add_parser(
        "reset",
        parents=[common],
        help="Reset to content_0001, cycle 1.",
    )
    reset_parser.set_defaults(func=cmd_reset)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
