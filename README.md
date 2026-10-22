# Menya Vocab — Daily TikTok publisher

This repository is the **daily publisher** for the Menya Icyongereza TikTok
channel [@perime.rw250](https://www.tiktok.com/@perime.rw250).

Every day it renders one sentence video, commits the mp4 so Buffer can fetch
it over a public raw URL, and schedules the post on TikTok for **18:30
Africa/Kigali** with a **blank caption**.

> Why a standalone repo? GitHub does not fire the `schedule` trigger on public
> forks, so the daily job cannot live in a fork of the upstream content repo
> (`primeherve/menya-vocab-video`). This repository is not a fork, so the cron
> trigger genuinely runs.

## How it works

| Step | Detail |
|---|---|
| Trigger | `schedule` cron `0 13 * * *` (13:00 UTC = 15:00 Africa/Kigali) |
| Render | `generate.py` draws the 1080×1920 frame and encodes the mp4 |
| Publish | `scripts/buffer_post.py` creates a Buffer post with `text: ""` and `mode: customScheduled` |
| Time | 18:30 Africa/Kigali (the slot the existing TikTok posts used) |
| Caption | Empty, matching the channel's previous posts |

## Rotation

The sentence rotation is `sentence_0001` → … → `sentence_0012` → back to
`sentence_0001`. Every video holds 5 or 6 English→Kinyarwanda pairs (10–12
text lines); the count comes from the layout planner, not a fixed rule.

- `data/sentence_rotation_state.json` — the next sentence and the cycle.
- `data/sentence_rotation_manifest.json` — the 12 ordered jobs.
- `data/sentence_chunks/sentence_chunk_01.json` — the 72 curated pairs.
- `data/buffer_queue.json` — Buffer post ids and due times (idempotency ledger).

The state advances **only after** Buffer accepts the post. If Buffer is down,
the next run repeats the same sentence video instead of skipping it.

## Duplicate protection

Two independent guards:

1. **Repository ledger** — `data/buffer_queue.json` maps
   `content_id@cycle-N` to the Buffer post id, so a rerun cannot schedule the
   same sentence twice.
2. **Buffer lookup** — before creating a post, the script queries the channel
   for a still-pending post whose asset is the same mp4 file name. This covers
   the window where Buffer accepted the post but the run died before the
   ledger was committed.

## Setup

```bash
pip install -r requirements.txt
export BUFFER_API_KEY="<your Buffer API key>"
python scripts/rotation.py next \
  --manifest data/sentence_rotation_manifest.json \
  --state data/sentence_rotation_state.json \
  --out data/.sentence_job.json \
  --pending data/.sentence_pending.json
python generate.py --batch-json data/.sentence_job.json \
  --note 'Iyi app yitwa "Menya Icyongereza App" wayisanga kuri Play Store yigisha icyongereza' \
  --brand "Menya Icyongereza App" --duration 6 \
  --preview preview/sentence_0001.png --out output/sentence_0001.mp4 --video
python scripts/buffer_post.py --content-id sentence_0001 --cycle 1 \
  --video-url https://raw.githubusercontent.com/hervecoder/menya-vocab-daily/main/output/sentence_0001.mp4 \
  --post-time 18:30 --ledger data/buffer_queue.json
```

## Manual runs

**Actions → Menya Vocab Sentences Daily → Run workflow.** Leave every field
as-is to publish the next sentence video, or set `publish` to `false` to
render and validate the Buffer payload without scheduling anything.

`content_id` replays one specific sentence video; a replay is never posted to
Buffer, so it cannot create a duplicate.

## Secret

`BUFFER_API_KEY` — the Buffer API key for the account that owns
`perime.rw250`. It is stored as a GitHub Actions repository secret and is
never written to the repository.

## Credits

Video content, fonts (Poppins, OFL) and music come from the upstream
`primeherve/menya-vocab-video` project.
