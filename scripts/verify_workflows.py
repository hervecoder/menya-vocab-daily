"""Parse both workflows and assert the trigger contract.

No network. Fails loudly on YAML errors or on a trigger that could double-post.
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


print("WORKFLOW YAML")
for name in ("menyavocab_words_daily.yml", "menyavocab_sentences_daily.yml"):
    path = ROOT / ".github" / "workflows" / name
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        check(f"{name} parses", isinstance(doc, dict))
    except Exception as exc:  # noqa: BLE001
        doc = None
        check(f"{name} parses", False, str(exc))
    if not doc:
        continue

    # PyYAML turns the bare `on:` key into True, so look for either spelling.
    triggers = doc.get("on", doc.get(True))
    keys = set(triggers or {}) if isinstance(triggers, dict) else set()
    print(f"       {name}: triggers={sorted(map(str, keys))}")

    has_schedule = "schedule" in keys
    has_dispatch = "workflow_dispatch" in keys
    check(f"{name} has workflow_dispatch", has_dispatch)
    check(f"{name} has NO schedule trigger", not has_schedule, "would double-post")

    job = (doc.get("jobs") or {}).get("build") or {}
    steps = job.get("steps") or []
    names = [s.get("name", "") for s in steps]
    check(f"{name} has a Buffer step", any("Buffer" in n for n in names))
    check(f"{name} commits the video before Buffer", any("Commit video" in n for n in names))
    check(f"{name} advances rotation after Buffer", any("Advance" in n for n in names))
    check(f"{name} runs on ubuntu", job.get("runs-on") == "ubuntu-latest")

    # Critical ordering: Buffer must come after the mp4 commit and before advance.
    idx_commit = next(i for i, n in enumerate(names) if "Commit video" in n)
    idx_buffer = next(
        i for i, n in enumerate(names) if n.startswith("Schedule") and "Buffer" in n
    )
    idx_advance = next(i for i, n in enumerate(names) if "Advance" in n)
    check(
        f"{name} order commit < buffer < advance",
        idx_commit < idx_buffer < idx_advance,
        f"{idx_commit} / {idx_buffer} / {idx_advance}",
    )

    if "words" in name:
        env = job.get("env") or {}
        for key in ("NOTE", "BRAND", "DURATION", "POST_TIME", "PUBLISH"):
            check(f"{name} env {key} has a fallback", "||" in str(env.get(key, "")))
        inputs = (triggers or {}).get("workflow_dispatch", {}).get("inputs", {})
        for key in ("content_id", "publish", "note", "brand", "duration", "post_time"):
            check(f"{name} declares input '{key}'", key in inputs)
        check(
            f"{name} reads the word manifest",
            any("rotation_manifest.json" in str(s) for s in steps),
        )
        check(
            f"{name} never reads the sentence manifest",
            not any("sentence_rotation_manifest" in str(s) for s in steps),
        )

print()
print(f"{fail} failed")
sys.exit(1 if fail else 0)
