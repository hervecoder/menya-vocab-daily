"""Local pre-flight for the word rotation database.

Same rules the workflow enforces on CI, so a bad copy fails here first:
1098 unique 7-word videos, 4 chunks, no repeated word inside a video, and no
word shared with the immediately preceding video (after the first 10).
"""
import json
from pathlib import Path

manifest = json.loads(Path("data/rotation_manifest.json").read_text(encoding="utf-8"))
jobs = manifest["jobs"]
chunks = manifest["chunks"]

assert len(jobs) == 1098, f"{len(jobs)} jobs"
assert len(chunks) == 4, f"{len(chunks)} chunks"
assert manifest["meta"]["unique_videos"] == 1098

cache = {}
seen = set()
prev = None
for i, entry in enumerate(jobs):
    cid = int(entry["chunk_id"])
    if cid not in cache:
        cache[cid] = json.loads(
            (Path("data") / chunks[cid - 1]["file"]).read_text(encoding="utf-8")
        )
    job = cache[cid]["jobs"][int(entry["job_index"])]
    assert job["id"] == entry["id"], f"mismatch at {i}: {job['id']} != {entry['id']}"

    pairs = job["pairs"]
    assert len(pairs) == 7, f"{entry['id']} has {len(pairs)} pairs"

    signature = tuple(
        sorted(
            (
                str(p["english"]).strip().casefold(),
                str(p["kinyarwanda"]).strip().casefold(),
            )
            for p in pairs
        )
    )
    assert signature not in seen, f"duplicate signature {entry['id']}"
    seen.add(signature)

    words = {str(p["english"]).strip().casefold() for p in pairs}
    assert len(words) == 7, f"{entry['id']} repeats a word"
    if prev is not None and i >= 10:
        overlap = prev & words
        assert not overlap, f"{entry['id']} overlaps previous: {sorted(overlap)}"
    prev = words

state = json.loads(Path("data/rotation_state.json").read_text(encoding="utf-8"))
print(f"OK: {len(jobs)} unique 7-word videos across {len(chunks)} chunks")
print(
    "next: %s  cycle %s  |  done %s/%s"
    % (
        state["next_content_id"],
        state["cycle"],
        state["jobs_done_in_cycle"],
        state["total_jobs"],
    )
)
