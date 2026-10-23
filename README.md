# Menya Vocab — Daily TikTok publisher

This repository is the **daily publisher** for the Menya Icyongereza TikTok
channel [@perime.rw250](https://www.tiktok.com/@perime.rw250).

Every day it renders one **word** video — exactly 7 English→Kinyarwanda pairs —
commits the mp4 so Buffer can fetch it over a public raw URL, and schedules the
post on TikTok for **18:30 Africa/Kigali** with a **blank caption**.

> **Sentence videos are paused for a quality upgrade.** The sentence track is
> kept in this repository and still runs manually, but it is no longer
> scheduled and no longer receives the daily slot. The daily job now dispatches
> `menyavocab_words_daily.yml`.

## Tracks

| Track | Workflow | Status | Shape |
|---|---|---|---|
| Words | `menyavocab_words_daily.yml` | **live daily** | 7 pairs, `content_0001` … `content_1098` |
| Sentences | `menyavocab_sentences_daily.yml` | paused (manual only) | 5–6 pairs, `sentence_0001` … `sentence_0012` |

Both share `generate.py`, `scripts/rotation.py`, `scripts/buffer_post.py`, the
fonts/background/music assets and the same `data/buffer_queue.json` ledger
(entries are keyed `content_id@cycle-N`, so the two id spaces never collide).

## How it works

The daily job is a **queue top-up worker**, not a one-shot publisher. Buffer
always holds five future posts; a run adds exactly as many as are missing.

| Step | Detail |
|---|---|
| Trigger | Cloudflare cron-web POSTs `workflow_dispatch` at `0 13 * * *` — 13:00 Africa/Kigali (11:00 UTC) |
| Plan | `scripts/queue_status.py` counts future posts and prints `add = 5 - depth` |
| Pick | `scripts/rotation.py next` selects the next word video and writes a pending marker |
| Render | `generate.py` draws the 1080×1920 frame and encodes the mp4 |
| Commit | `output/<id>.mp4` + `preview/<id>.png` are pushed **before** Buffer is called |
| Publish | `scripts/buffer_post.py` creates a Buffer post with `text: ""` and `mode: customScheduled` |
| Advance | Rotation advances **only after** Buffer accepts the post |
| Time | 18:30 Africa/Kigali, on the first free day |
| Caption | Empty, matching the channel's previous posts |

Plan → pick → render → commit → publish → advance runs in a loop, `add` times,
inside one job. A cold start fills all five in a single run (~70 s); a normal
day adds one; if three days of runs fail, the next run adds three and the
buffer is whole again instead of limping along one day at a time.

The commit → publish → advance order is deliberate. If Buffer fails, the
rotation is not advanced, so the next run repeats the same video instead of
silently skipping it.

### Five posts ahead

`data/buffer_queue.json` is the source of truth for the queue. `queue_status.py`
reports:

```
depth = posts whose due_at is still in the future (failed posts excluded)
add   = max(0, 5 - depth)
```

Scheduling uses the **first free day**, not "today or tomorrow": Kigali dates
already occupied by a live ledger post are skipped. With five posts queued, a
new run lands on the sixth day; if a day is missing, that day is filled.

On 2026-10-23 the queue was preloaded with `content_0005` … `content_0009` for
24–28 October 2026, and the rotation moved to `content_0010`.

The 14:30 safety net asks the same depth question and re-dispatches the top-up
only when the queue is short, so a failed primary run is retried without
touching a healthy queue.

### ffmpeg

The `ubuntu-24.04` runner image has **no ffmpeg**, and installing it with apt
pulls 99 packages / 94 MB from `azure.archive.ubuntu.com`. On 2026-10-23 that
mirror crawled and a 300 s cap killed the daily run (exit 124), losing the
post.

The workflows therefore never call apt. They install `imageio-ffmpeg` from
PyPI — one ~30 MB wheel — and symlink its static binary onto `PATH`, falling
back to the BtbN GPL static build from GitHub releases. Both paths must pass a
real `libx264` smoke encode, so a broken binary fails in seconds rather than
at render time.

### Triggers are explicit, never doubled

Neither workflow declares a `schedule:` block. GitHub's `schedule` event is
**unreliable on this account** — a `*/5` probe in this repository produced only
2 runs across ~5.5 hours — and keeping it would risk a second dispatch on the
days it did fire, producing two videos and two posts.

`cron-web` on `ytcommentmanager.ndatimanah.workers.dev` is the single trigger:

- Its own Cloudflare cron trigger is `*/1 * * * *` (see that repo's `wrangler.toml`).
- Job id `1` holds the daily dispatch to **`menyavocab_words_daily.yml`**.
  Its `body` carries every input explicitly, including `content_id: ""`
  which means "auto-next word video". `count` is omitted so the workflow
  defaults to `auto` (top the queue up to five).
- Job id `2` runs at `30 14 * * *` and dispatches
  `menyavocab_words_safety.yml`, which re-dispatches the daily workflow only
  when the queue depth is below five.
- The GitHub token lives in the job's `headers` field, readable only with the
  cron-web key (`D:\kinyarwnda\AI AGENT\cron-web-token.txt`).

Inspect it at `https://ytcommentmanager.ndatimanah.workers.dev/cron` or via
`GET /cron/api/jobs` with the `x-cron-key` header.

### Endpoint note

The dispatch URL must be the *workflow* path:

```
https://api.github.com/repos/hervecoder/menya-vocab-daily/actions/workflows/menyavocab_words_daily.yml/dispatches
```

`POST /repos/{owner}/{repo}/dispatches` is a different endpoint
(`repository_dispatch`): it rejects `ref`/`inputs`, demands `event_type`, and
returns `422`. A correct workflow dispatch returns **`204 No Content`**.

## Word rotation

`content_0001` → … → `content_1098`, then the cycle increments and it starts
again from `content_0001`. The database is validated on every run:

- 1098 unique video signatures (no two videos share the same 7 pairs)
- 4 chunks, 7 pairs per video, no word repeated inside a video
- no word shared with the immediately preceding video

- `data/rotation_state.json` — next content and cycle (currently `content_0010`).
- `data/rotation_manifest.json` — the 1098 ordered jobs.
- `data/chunks/vocabulary_chunk_0{1..4}.json` — the pair data.
- `data/buffer_queue.json` — Buffer post ids and due times (idempotency ledger).

## Sentence rotation (paused)

`sentence_0001` → … → `sentence_0012`. Each video holds 5 or 6 pairs; the count
comes from the layout planner, not a fixed rule.

- `data/sentence_rotation_state.json` — the next sentence and the cycle.
- `data/sentence_rotation_manifest.json` — the 12 ordered jobs.
- `data/sentence_chunks/sentence_chunk_01.json` — the 72 curated pairs.

The state advances **only after** Buffer accepts the post, exactly like the
word track. Until the quality pass is finished, this workflow has no trigger
and is not referenced by cron-web.

## Duplicate protection

Two independent guards:

1. **Repository ledger** — `data/buffer_queue.json` maps
   `content_id@cycle-N` to the Buffer post id, so a rerun cannot schedule the
   same video twice.
2. **Buffer lookup** — before creating a post, the script queries the channel
   for a still-pending post whose asset is the same mp4 file name. This covers
   the window where Buffer accepted the post but the run died before the
   ledger was committed.

## Setup

```bash
pip install -r requirements.txt
export BUFFER_API_KEY="<your Buffer API key>"
python scripts/rotation.py next \
  --manifest data/rotation_manifest.json \
  --state data/rotation_state.json \
  --out data/.current_job.json \
  --pending data/.rotation_pending.json
python generate.py --batch-json data/.current_job.json \
  --note 'Iyi app yitwa "Menya Icyongereza App" wayisanga kuri Play Store yigisha icyongereza' \
  --brand "Menya Icyongereza App" --duration 6 \
  --preview preview/content_0005.png --out output/content_0005.mp4 --video
python scripts/buffer_post.py --content-id content_0005 --cycle 1 \
  --video-url https://raw.githubusercontent.com/hervecoder/menya-vocab-daily/main/output/content_0005.mp4 \
  --post-time 18:30 --ledger data/buffer_queue.json
```

Local pre-flight (no network):

```bash
python scripts/verify_word_db.py     # 1098 unique 7-word videos
python scripts/verify_workflows.py   # YAML parses; no schedule; step order
```

## Manual runs

**Actions → Menya Vocab Words Daily → Run workflow.** Leave every field as-is
to top the queue back up to five, or set `publish` to `false` to render and
validate one video without scheduling anything.

`count` controls the batch: `auto` (default) adds only what is missing, while a
number forces exactly that many videos in one run — useful for a deliberate
preload.

`content_id` replays one specific video; a replay is never posted to Buffer, so
it cannot create a duplicate.

The sentence workflow still runs the same way from
**Actions → Menya Vocab Sentences (maintenance — paused)** when a sentence
video needs to be re-rendered for the quality pass.

## Secret

`BUFFER_API_KEY` — the Buffer API key for the account that owns
`perime.rw250`. It is stored as a GitHub Actions repository secret and is
never written to the repository.

## Credits

Video content, fonts (Poppins, OFL) and music come from the upstream
`primeherve/menya-vocab-video` project.
