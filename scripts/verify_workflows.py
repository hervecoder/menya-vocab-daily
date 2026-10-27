"""Parse the workflows and assert the contract that keeps the channel safe.

No network. Fails loudly on YAML errors, a second trigger, or a broken order.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
fail = 0


def check(label, cond, detail=""):
    global fail
    if cond:
        print(f"  ok   {label}")
    else:
        fail += 1
        print(f"  FAIL {label}  -> {detail}")


def load(name):
    path = ROOT / ".github" / "workflows" / name
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        check(f"{name} parses", False, str(exc))
        return None
    check(f"{name} parses", isinstance(doc, dict))
    return doc


def triggers_of(doc):
    # PyYAML turns the bare `on:` key into True, so look for either spelling.
    trig = doc.get("on", doc.get(True))
    return set(trig or {}) if isinstance(trig, dict) else set()


def steps_of(doc):
    job = (doc.get("jobs") or {}).get("build") or {}
    return job.get("steps") or []


print("WORDS")
words = load("menyavocab_words_daily.yml")
if words:
    keys = triggers_of(words)
    check("no schedule trigger", "schedule" not in keys, "would double-post")
    check("has workflow_dispatch", "workflow_dispatch" in keys)

    inputs = (words.get("on", words.get(True)) or {}).get("workflow_dispatch", {}).get("inputs", {})
    for key in ("content_id", "publish", "note", "brand", "duration", "post_time", "mode", "count"):
        check(f"declares input '{key}'", key in inputs)

    steps = steps_of(words)
    names = [s.get("name", "") for s in steps]
    ids = {s.get("id"): s for s in steps if s.get("id")}

    check("has the plan step", "plan" in ids)
    check("has the render loop", "run" in ids)

    plan = str(ids.get("plan", {}).get("run", ""))
    check("plan calls queue_status.py", "queue_status.py" in plan)
    check("plan honours an explicit count", "add=$COUNT" in plan)
    check("dry runs render a single video", 'add=1' in plan and "publish" in plan.lower())

    runner = str(ids.get("run", {}).get("run", ""))
    check("loop calls rotation.py next", "rotation.py next" in runner)
    check("loop calls buffer_post.py", "buffer_post.py" in runner)
    check("loop uses the queue by default", '--mode "$MODE"' in runner)
    check("mode falls back to queue", "inputs.mode || 'queue'" in str((words.get("jobs") or {}).get("build", {}).get("env") or {}))
    check("loop calls rotation.py advance", "rotation.py advance" in runner)
    check("loop honours the replay id", "rotation.py select" in runner)
    check(
        "buffer runs before advance",
        runner.index("buffer_post.py") < runner.index("rotation.py advance"),
        "advancing first would skip a video on a Buffer failure",
    )
    check("loop is gated on add", ids.get("run", {}).get("if") == "steps.plan.outputs.add != '0'")
    check(
        "loop exposes the Buffer secret",
        "BUFFER_API_KEY" in (ids.get("run", {}).get("env") or {}),
        "buffer_post.py would exit on a missing key",
    )

    body = str(steps)
    check("reads the word manifest", "rotation_manifest.json" in body)
    check("never reads the sentence manifest", "sentence_rotation_manifest" not in body)

    ffmpeg = next((s for s in steps if s.get("name") == "Install ffmpeg"), {})
    ff = str(ffmpeg.get("run", ""))
    check("ffmpeg avoids apt-get update", "apt-get update" not in ff)
    check("ffmpeg uses a static build", "imageio-ffmpeg" in ff)
    check("ffmpeg smoke-tests libx264", "libx264" in ff)

    env = (words.get("jobs") or {}).get("build", {}).get("env") or {}
    for key in ("NOTE", "BRAND", "DURATION", "POST_TIME", "PUBLISH", "COUNT"):
        check(f"env {key} has a fallback", "||" in str(env.get(key, "")))

print("\nSAFETY NET")
safety = load("menyavocab_words_safety.yml")
if safety:
    keys = triggers_of(safety)
    check("no schedule trigger", "schedule" not in keys)
    check("has workflow_dispatch", "workflow_dispatch" in keys)

    steps = (safety.get("jobs") or {}).get("check", {}).get("steps") or []
    body = str(steps)
    check("reads the queue depth", "queue_status.py" in body)
    check("re-dispatches the daily workflow", "gh workflow run menyavocab_words_daily.yml" in body)
    check("forwards count", "-f count=" in body)
    check(
        "can dispatch workflows",
        (safety.get("permissions") or {}).get("actions") == "write",
    )

print("\nSENTENCES (paused)")
sent = load("menyavocab_sentences_daily.yml")
if sent:
    keys = triggers_of(sent)
    check("no schedule trigger", "schedule" not in keys, "sentences are parked")
    check("still manually runnable", "workflow_dispatch" in keys)
    names = [s.get("name", "") for s in steps_of(sent)]
    check("keeps its Buffer step", any("Buffer" in n for n in names))

print()
print(f"{fail} failed")
sys.exit(1 if fail else 0)
