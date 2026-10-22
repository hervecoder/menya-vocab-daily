#!/usr/bin/env python3
"""
Menya Icyongereza - vocabulary video generator.

Renders a 1080x1920 video frame that looks like the Menya Icyongereza app
screen (phone mockup background), with either 7 English word / Kinyarwanda
rows or 5-6 sentence pairs (10-12 lines), plus an edge-to-edge yellow promo
banner.  A final partial job may have fewer rows/pairs; those cards are centred.

Everything is drawn at 1.5x the source mockup so text stays sharp.

Usage:
    python generate.py                         # built-in test content
    python generate.py --batch-json data/vocabulary_pilot_videos.json --video-id word_01
    python generate.py --batch-json data/vocabulary_pilot_videos.json --video-id sentence_01
    python generate.py --pairs "Book=Igitabo,Water=Amazi"
    python generate.py --mode sentence --pairs "Where does it hurt?=Uri kubabara he?..."
    python generate.py --note "Some text" --duration 6
    python generate.py --video                 # also encode the mp4
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- paths / canvas
ROOT = Path(__file__).resolve().parent
BG_PATH = ROOT / "assets" / "img" / "background.png"
FONT_DIR = ROOT / "assets" / "fonts"
OUT_DIR = ROOT / "output"
PREVIEW_DIR = ROOT / "preview"

SCALE = 1.5
W, H = 1080, 1920

# Phone screen body measured from the 720x1280 mockup (x 32..685, y 183..1115)
BODY = (
    round(32 * SCALE),      # left
    round(183 * SCALE),     # top
    round(685 * SCALE),     # right
    round(1115 * SCALE),    # bottom
)

# Top edge of the mockup's bottom tab bar (the divider at source y=1117).
NAV_BAR_TOP = round(1117 * SCALE)

# ---------------------------------------------------------------- design tokens
GREEN = "#58CC02"
GREEN_DARK = "#46A302"
GREEN_LIGHT = "#D7FFB8"
TEXT = "#3C3C3C"
MUTED = "#8A8A8A"
BORDER = "#E5E5E5"
YELLOW = "#FFF9C4"
INK = "#1E1E1E"
WHITE = "#FFFFFF"

SIDE_PAD = 36
ROW_GAP = 20
ROW_MAX_H = 184
PROMO_PAD_X = 8
PROMO_PAD_Y = 18
PROMO_FONT_SIZE = 40
PROMO_LINE_H = 54

# Sentence cards size themselves from their content instead of using one tall
# fixed row. A line may only wrap after it has shrunk to the readable minimum.
SENTENCE_MAX_FONT = 46
SENTENCE_MIN_FONT = 24
SENTENCE_PAD_Y = 26
SENTENCE_MIN_CARD_H = 132
SENTENCE_GAP_MIN = 18
SENTENCE_GAP_MAX = 64

# ---------------------------------------------------------------- defaults
DEFAULT_PAIRS = "Book=Igitabo,Water=Amazi,House=Inzu,Friend=Inshuti,School=Ishuri,Teacher=Umwarimu,Market=Isoko"
DEFAULT_NOTE = (
    'Iyi app yitwa "Menya Icyongereza App" wayisanga kuri Play Store '
    "yigisha icyongereza"
)
DEFAULT_BRAND = "Menya Icyongereza App"


@lru_cache(maxsize=None)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


def tracked_text(draw, xy, text, fnt, fill, tracking=0.0, anchor="la"):
    """Draw text with letter-spacing (Pillow has no native tracking)."""
    x, y = xy
    if tracking == 0:
        draw.text((x, y), text, font=fnt, fill=fill, anchor=anchor)
        return
    widths = [fnt.getlength(ch) for ch in text]
    total = sum(widths) + tracking * max(0, len(text) - 1)
    if anchor[0] == "m":
        x -= total / 2
    elif anchor[0] == "r":
        x -= total
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=fnt, fill=fill, anchor=anchor)
        x += w + tracking


def parse_pairs(raw: str, max_pairs: int = 7):
    chunks = [c.strip() for c in raw.replace("\r", "").split(";")]
    chunks = [c for chunk in chunks for c in chunk.split("\n")]
    chunks = [c.strip() for c in chunks if c.strip()]
    if len(chunks) == 1 and "," in chunks[0]:
        chunks = [c.strip() for c in chunks[0].split(",") if c.strip()]

    pairs = []
    for chunk in chunks:
        for sep in ("=", "|"):
            if sep in chunk:
                eng, kin = chunk.split(sep, 1)
                break
        else:
            raise SystemExit(f"Bad pair (use English=Kinyarwanda): {chunk!r}")
        eng, kin = eng.strip(), kin.strip()
        if eng and kin:
            pairs.append((eng, kin))
    if not pairs:
        raise SystemExit("No word pairs given.")
    return pairs[:max_pairs]


def fit_font(text, font_name, start, minimum, max_width):
    size = start
    while size > minimum:
        f = font(font_name, size)
        if f.getlength(text) <= max_width:
            return f
        size -= 1
    return font(font_name, minimum)


def wrap_to_width(text, fnt, max_width):
    words = text.split()
    lines, cur = [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if fnt.getlength(trial) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def wrap_balanced(text, fnt, max_width, protect=()):
    """Wrap lines so they look even instead of leaving one orphan word.

    Minimises the sum of squared empty space on every line. Explicit
    newlines in the input are respected. Quoted phrases and any phrase
    listed in ``protect`` are never split across lines.
    """
    if "\n" in text:
        out = []
        for part in text.split("\n"):
            part = part.strip()
            if part:
                out.extend(wrap_balanced(part, fnt, max_width, protect))
        return out or [""]

    import re

    words = re.findall(r'"[^"]*"|[^\s]+', text)

    # keep multi-word protected phrases together
    phrases = [p.split() for p in protect if p]
    merged = []
    i = 0
    while i < len(words):
        for parts in phrases:
            chunk = words[i : i + len(parts)]
            if len(chunk) == len(parts) and [
                w.strip('"').lower() for w in chunk
            ] == [p.lower() for p in parts]:
                merged.append(" ".join(chunk))
                i += len(parts)
                break
        else:
            merged.append(words[i])
            i += 1
    words = merged

    if not words:
        return [""]

    widths = [fnt.getlength(w) for w in words]
    space = fnt.getlength(" ")
    n = len(words)

    # cost of putting words i..j on one line (None if it does not fit)
    def line_cost(i, j):
        w = sum(widths[i : j + 1]) + space * (j - i)
        if w > max_width:
            return None, w
        return (max_width - w) ** 2, w

    best = [float("inf")] * (n + 1)
    best[0] = 0
    prev = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            cost, w = line_cost(i, j - 1)
            if cost is None:
                break
            cand = best[i] + cost
            if cand < best[j]:
                best[j] = cand
                prev[j] = i

    # reconstruct
    lines = []
    j = n
    while j > 0:
        i = prev[j]
        lines.append(" ".join(words[i:j]))
        j = i
    return list(reversed(lines))


def rounded_shadow(base, box, radius, offset=6, alpha=26):
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    x0, y0, x1, y1 = box
    d.rounded_rectangle(
        (x0, y0 + offset, x1, y1 + offset), radius=radius, fill=(20, 20, 20, alpha)
    )
    return Image.alpha_composite(base, shadow)


def draw_row(canvas, box, index, english, kinyarwanda):
    x0, y0, x1, y1 = box
    h = y1 - y0
    radius = 24
    canvas = rounded_shadow(canvas, box, radius)
    d = ImageDraw.Draw(canvas)

    # card
    d.rounded_rectangle(box, radius=radius, fill=WHITE, outline=BORDER, width=2)

    # number badge (3D: darker edge under the green face)
    badge = 64
    bx = x0 + 26
    by = y0 + (h - badge) // 2
    d.rounded_rectangle(
        (bx, by + 4, bx + badge, by + badge + 4), radius=18, fill=GREEN_DARK
    )
    d.rounded_rectangle((bx, by, bx + badge, by + badge), radius=18, fill=GREEN)
    num = font("Poppins-ExtraBold.ttf", 30)
    d.text(
        (bx + badge / 2, by + badge / 2 + 1),
        str(index),
        font=num,
        fill=WHITE,
        anchor="mm",
    )

    # English (left) and Kinyarwanda (right), baseline aligned
    text_left = bx + badge + 26
    text_right = x1 - 30
    available = text_right - text_left

    f_eng = fit_font(english, "Poppins-ExtraBold.ttf", 46, 26, available * 0.62)
    f_kin = fit_font(kinyarwanda, "Poppins-Bold.ttf", 42, 24, available * 0.55)

    eng_w = f_eng.getlength(english)
    kin_w = f_kin.getlength(kinyarwanda)

    baseline = y0 + h / 2 + 0.35 * f_eng.size

    d.text((text_left, baseline), english, font=f_eng, fill=TEXT, anchor="ls")
    d.text((text_right, baseline), kinyarwanda, font=f_kin, fill=GREEN_DARK, anchor="rs")

    # dotted leader between the two columns
    dot_start = text_left + eng_w + 22
    dot_end = text_right - kin_w - 22
    if dot_end - dot_start >= 40:
        dot_y = y0 + h / 2 + 12
        r = 3
        step = 15
        x = dot_start
        while x <= dot_end:
            d.ellipse((x - r, dot_y - r, x + r, dot_y + r), fill="#D5D5D5")
            x += step

    return canvas


def sentence_text_bounds(box):
    """Return the left/right edge shared by the sentence layout and fitting."""
    x0, _y0, x1, _y1 = box
    text_left = x0 + 24 + 58 + 22
    return text_left, x1 - 26


def layout_sentence_row(english, kinyarwanda, available):
    """Pick the readable font size and line count for one sentence card.

    One line is always preferred. The text only wraps after it has been reduced
    to SENTENCE_MIN_FONT, so a long sentence gets a second line instead of
    becoming unreadably small. The card height is then derived from the actual
    line count, not from a fixed row height.
    """
    usable = available - 4
    chosen_size = None
    eng_lines = []
    kin_lines = []

    for size in range(SENTENCE_MAX_FONT, SENTENCE_MIN_FONT - 1, -1):
        kin_size = max(SENTENCE_MIN_FONT - 2, size - 2)
        f_eng = font("Poppins-ExtraBold.ttf", size)
        f_kin = font("Poppins-Bold.ttf", kin_size)
        if (
            f_eng.getlength(english) <= usable
            and f_kin.getlength(kinyarwanda) <= usable
        ):
            chosen_size = size
            eng_lines = [english]
            kin_lines = [kinyarwanda]
            break

    if chosen_size is None:
        chosen_size = SENTENCE_MIN_FONT
        f_eng = font("Poppins-ExtraBold.ttf", chosen_size)
        f_kin = font("Poppins-Bold.ttf", max(12, chosen_size - 2))
        eng_lines = wrap_balanced(english, f_eng, usable)
        kin_lines = wrap_balanced(kinyarwanda, f_kin, usable)

    if len(eng_lines) > 3 or len(kin_lines) > 3:
        raise SystemExit(
            "Sentence needs more than three lines at the minimum readable "
            f"font: {english!r} / {kinyarwanda!r}"
        )

    kin_size = max(12, chosen_size - 2)
    eng_lh = chosen_size + 6
    kin_lh = kin_size + 6
    block_h = len(eng_lines) * eng_lh + 12 + len(kin_lines) * kin_lh
    card_h = max(SENTENCE_MIN_CARD_H, block_h + 2 * SENTENCE_PAD_Y)
    return {
        "size": chosen_size,
        "eng_lines": eng_lines,
        "kin_lines": kin_lines,
        "eng_lh": eng_lh,
        "kin_lh": kin_lh,
        "block_h": block_h,
        "card_h": card_h,
    }


def layout_sentence_rows(pairs, available):
    return [layout_sentence_row(eng, kin, available) for eng, kin in pairs]


def sentence_card_gap(heights, space):
    """Return the gap the renderer would use for this stack of cards."""
    n = len(heights)
    if n <= 1:
        return 0
    spare = (space - sum(heights)) // (n - 1)
    return max(SENTENCE_GAP_MIN, min(SENTENCE_GAP_MAX, spare))


def sentence_video_layout(pairs, context):
    """Lay out one complete sentence video and report whether it fits.

    This is the single source of truth shared by the renderer and the sentence
    planner.  The planner uses it to decide whether a candidate 5- or 6-pair
    group can be published without unreadable text.
    """
    layouts = layout_sentence_rows(pairs, context["text_width"])
    heights = [layout["card_h"] for layout in layouts]
    gap = sentence_card_gap(heights, context["space"])
    used_h = sum(heights) + gap * (len(heights) - 1)
    return {
        "layouts": layouts,
        "heights": heights,
        "gap": gap,
        "used_h": used_h,
        "space": context["space"],
        "fits": used_h <= context["space"],
    }


def draw_sentence_row(canvas, box, index, layout):
    """Sentence card sized to its actual wrapped line count."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    radius = 24
    canvas = rounded_shadow(canvas, box, radius)
    d = ImageDraw.Draw(canvas)

    d.rounded_rectangle(box, radius=radius, fill=WHITE, outline=BORDER, width=2)

    # number badge
    badge = 58
    bx = x0 + 24
    by = y0 + (h - badge) // 2
    d.rounded_rectangle(
        (bx, by + 4, bx + badge, by + badge + 4), radius=16, fill=GREEN_DARK
    )
    d.rounded_rectangle((bx, by, bx + badge, by + badge), radius=16, fill=GREEN)
    num = font("Poppins-ExtraBold.ttf", 28)
    d.text(
        (bx + badge / 2, by + badge / 2 + 1),
        str(index),
        font=num,
        fill=WHITE,
        anchor="mm",
    )

    text_left, _text_right = sentence_text_bounds(box)
    f_eng = font("Poppins-ExtraBold.ttf", layout["size"])
    f_kin = font("Poppins-Bold.ttf", max(12, layout["size"] - 2))
    block_h = layout["block_h"]
    if block_h > h:
        raise SystemExit(f"Sentence card {index} is shorter than its text block")

    y = y0 + (h - block_h) / 2
    for line in layout["eng_lines"]:
        d.text((text_left, y), line, font=f_eng, fill=TEXT, anchor="la")
        y += layout["eng_lh"]

    y += 12
    for line in layout["kin_lines"]:
        d.text((text_left, y), line, font=f_kin, fill=GREEN_DARK, anchor="la")
        y += layout["kin_lh"]

    return canvas


def promo_segments(line, brand):
    """Split a line into plain / highlighted runs around the brand name."""
    if not brand:
        return [(line, False)]
    low = line.lower()
    needle = brand.lower()
    out = []
    pos = 0
    while True:
        i = low.find(needle, pos)
        if i == -1:
            if pos < len(line):
                out.append((line[pos:], False))
            break
        start, end = i, i + len(brand)
        # keep the quotes around the app name inside the highlight
        if start > 0 and line[start - 1] in '"“':
            start -= 1
        if end < len(line) and line[end] in '"”':
            end += 1
        if start > pos:
            out.append((line[pos:start], False))
        out.append((line[start:end], True))
        pos = end
    return out or [(line, False)]


def draw_promo(canvas, box, lines, brand):
    x0, y0, x1, y1 = box
    d = ImageDraw.Draw(canvas)
    # edge-to-edge in-app banner: sharp corners, flat fill, no outline
    d.rectangle(box, fill=YELLOW)

    f_line = font("Poppins-Bold.ttf", PROMO_FONT_SIZE)
    f_brand = font("Poppins-ExtraBold.ttf", PROMO_FONT_SIZE)
    line_h = PROMO_LINE_H
    content_h = len(lines) * line_h
    cy = y0 + ((y1 - y0) - content_h) / 2

    for line in lines:
        segments = promo_segments(line, brand)
        total = sum(
            (f_brand if hot else f_line).getlength(text) for text, hot in segments
        )
        x = (x0 + x1) / 2 - total / 2
        for text, hot in segments:
            f = f_brand if hot else f_line
            d.text(
                (x, cy),
                text,
                font=f,
                fill=GREEN_DARK if hot else INK,
                anchor="la",
            )
            x += f.getlength(text)
        cy += line_h

    # only the bottom edge keeps a rule, like an in-app banner
    d.rectangle((x0, y1 - 2, x1, y1), fill=INK)

    return canvas


def frame_context(note: str, brand: str) -> dict:
    """Measure the screen areas that both the renderer and planner need."""
    left, top, right, bottom = BODY
    content_left = left + SIDE_PAD
    content_right = right - SIDE_PAD

    f_line = font("Poppins-Bold.ttf", PROMO_FONT_SIZE)
    banner_w = (right - 1) - left
    note_lines = wrap_balanced(
        note.strip(),
        f_line,
        banner_w - PROMO_PAD_X * 2,
        protect=(brand, "Play Store"),
    )
    promo_h = PROMO_PAD_Y * 2 + len(note_lines) * PROMO_LINE_H
    promo_bottom = NAV_BAR_TOP
    promo_top = promo_bottom - promo_h

    rows_top = top + 34 + 40 + 28
    rows_bottom_limit = promo_top - 42
    text_left, text_right = sentence_text_bounds(
        (content_left, 0, content_right, 0)
    )
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "content_left": content_left,
        "content_right": content_right,
        "note_lines": note_lines,
        "promo_top": promo_top,
        "promo_bottom": promo_bottom,
        "rows_top": rows_top,
        "rows_bottom_limit": rows_bottom_limit,
        "space": rows_bottom_limit - rows_top,
        "text_width": text_right - text_left,
    }


def render(pairs, note, brand, out_path: Path, mode: str = "word"):
    if not BG_PATH.exists():
        raise SystemExit(f"Missing background: {BG_PATH}")

    bg = Image.open(BG_PATH).convert("RGBA").resize((W, H), Image.Resampling.LANCZOS)
    canvas = bg.copy()

    context = frame_context(note, brand)
    left = context["left"]
    top = context["top"]
    right = context["right"]
    content_left = context["content_left"]
    content_right = context["content_right"]
    note_lines = context["note_lines"]
    promo_top = context["promo_top"]
    promo_bottom = context["promo_bottom"]
    rows_top = context["rows_top"]
    rows_bottom_limit = context["rows_bottom_limit"]
    n = len(pairs)

    # section label
    if mode == "sentence":
        label = f"INTERURO {len(pairs)} Z'ICYONGEREZA"
    else:
        label = f"AMAGAMBO {len(pairs)} Y'ICYONGEREZA"
    d = ImageDraw.Draw(canvas)
    f_label = font("Poppins-SemiBold.ttf", 24)
    tracked_text(
        d, (content_left + 3, top + 34), label, f_label, MUTED, tracking=3.2
    )

    if mode == "sentence":
        plan = sentence_video_layout(pairs, context)
        layouts = plan["layouts"]
        heights = plan["heights"]
        space = plan["space"]
        gap = plan["gap"]
        used_h = plan["used_h"]
        if not plan["fits"]:
            raise SystemExit(
                "Sentence cards do not fit between the label and promo banner"
            )
        y = rows_top + (space - used_h) // 2
        for i, (layout, card_h) in enumerate(zip(layouts, heights), start=1):
            box = (content_left, y, content_right, y + card_h)
            canvas = draw_sentence_row(canvas, box, i, layout)
            y += card_h + gap
    else:
        gap = ROW_GAP
        available_h = rows_bottom_limit - rows_top - (n - 1) * gap
        row_h = max(96, min(ROW_MAX_H, int(available_h / n)))
        used_h = n * row_h + (n - 1) * gap
        extra_h = max(0, (rows_bottom_limit - rows_top) - used_h)
        y = rows_top + extra_h // 2
        for i, (eng, kin) in enumerate(pairs, start=1):
            canvas = draw_row(
                canvas, (content_left, y, content_right, y + row_h), i, eng, kin
            )
            y += row_h + gap

    canvas = draw_promo(
        canvas,
        (left, promo_top, right - 1, promo_bottom),
        note_lines,
        brand,
    )

    out = canvas.convert("RGB")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path, "PNG", optimize=True)
    return out


def encode_video(frame_path: Path, out_path: Path, duration: float, song: Path | None):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found on PATH.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-framerate", "30", "-loop", "1", "-i", str(frame_path)]
    if song:
        cmd += ["-stream_loop", "-1", "-i", str(song)]
        cmd += [
            "-af",
            "loudnorm=I=-15:TP=-1.5:LRA=11",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
        ]
    cmd += [
        "-t",
        str(duration),
        "-r",
        "30",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "19",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return out_path


def pick_song(explicit: str):
    if explicit:
        song = Path(explicit)
        if not song.exists():
            raise SystemExit(f"Song not found: {song}")
        return song

    song_dir = ROOT / "assets" / "song"
    if not song_dir.exists():
        return None
    tracks = [
        p
        for p in sorted(song_dir.iterdir())
        if p.suffix.lower() in {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
    ]
    return random.choice(tracks) if tracks else None


def load_batch(path: str, video_id: str = "", preferred_mode: str = "word"):
    batch_path = Path(path)
    if not batch_path.exists():
        raise SystemExit(f"Batch JSON not found: {batch_path}")

    data = json.loads(batch_path.read_text(encoding="utf-8"))
    videos = data.get("videos") or []
    if not videos:
        raise SystemExit(f"No videos in batch JSON: {batch_path}")

    if video_id:
        video = next((v for v in videos if v.get("id") == video_id), None)
        if video is None:
            ids = ", ".join(v.get("id", "?") for v in videos)
            raise SystemExit(f"Video id {video_id!r} not found. Available: {ids}")
    else:
        video = next(
            (v for v in videos if v.get("mode") == preferred_mode), videos[0]
        )

    mode = video.get("mode", "word")
    expected = 7 if mode == "word" else 6
    pairs = [(p["english"], p["kinyarwanda"]) for p in video.get("pairs", [])]
    if not (1 <= len(pairs) <= expected):
        raise SystemExit(
            f"Batch {video.get('id')!r} has {len(pairs)} pairs; "
            f"{mode} mode needs 1-{expected} pairs."
        )

    return mode, pairs, video.get("id", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=DEFAULT_PAIRS)
    ap.add_argument("--batch-json", default="")
    ap.add_argument("--video-id", default="")
    ap.add_argument("--mode", choices=["word", "sentence"], default="word")
    ap.add_argument("--note", default=DEFAULT_NOTE)
    ap.add_argument("--brand", default=DEFAULT_BRAND)
    ap.add_argument("--duration", type=float, default=6.0)
    ap.add_argument("--song", default="")
    ap.add_argument("--video", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--preview", default="")
    args = ap.parse_args()

    if args.batch_json:
        mode, pairs, video_id = load_batch(
            args.batch_json, args.video_id, args.mode
        )
    else:
        mode = args.mode
        video_id = ""
        pairs = parse_pairs(args.pairs, 100 if mode == "sentence" else 7)
        if not (1 <= len(pairs) <= (6 if mode == "sentence" else 7)):
            raise SystemExit(
                "Sentence mode needs 1-6 English=Kinyarwanda pairs."
                if mode == "sentence"
                else "Word mode needs 1-7 English=Kinyarwanda pairs."
            )

    if args.preview:
        preview_path = Path(args.preview)
    elif video_id:
        preview_path = PREVIEW_DIR / f"{video_id}.png"
    else:
        preview_path = PREVIEW_DIR / "preview.png"

    render(pairs, args.note, args.brand, preview_path, mode=mode)
    print(f"mode:    {mode}")
    if video_id:
        print(f"batch:   {video_id} ({len(pairs)} pairs)")
    print(f"preview: {preview_path}")

    if args.video:
        if args.out:
            video_path = Path(args.out)
        else:
            stamp = int(time.time() * 1000)
            if video_id:
                video_path = OUT_DIR / f"menya_vocab_{video_id}_{stamp}.mp4"
            else:
                video_path = OUT_DIR / f"menya_vocab_{stamp}.mp4"
        song = pick_song(args.song)
        if song:
            print(f"song:    {song}")
        encode_video(preview_path, video_path, args.duration, song)
        print(f"video:   {video_path}")


if __name__ == "__main__":
    main()
