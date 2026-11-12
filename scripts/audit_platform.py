"""Offline diagnostic audit. FAIL means an observed gap, not a failed API call.

Uses isolated fixtures, no real credentials or billable provider requests.
Optional --render exercises the real FFmpeg export using synthetic silent audio.
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import stories
import story_writer
from story_captions import alignment_words
from story_validation import story_is_reasonable


def audit(render=False):
    rows = []

    def record(name, passed, evidence):
        rows.append(dict(check=name, passed=bool(passed), evidence=evidence))

    short = dict(title="An uneventful day",
                 hooks=["I opened the door.", "I walked inside.", "I arrived at home."],
                 setup=["I sat down."], ending=["Then I went to bed."])
    record("Reject very short 120-second scripts",
           not (story_writer.valid_story(short) and story_is_reasonable(short, 120)),
           "A complete narration of fewer than 20 words requested as 120 seconds.")
    repeated = {**short, "setup": short["setup"] * 2}
    record("Reject repeated sentences", not story_is_reasonable(repeated, 30),
           "An exact setup sentence is repeated twice.")
    for label, chars, times in (
        ("Reject backwards character timestamps", list("hello"), [0, .4, .3, .2, .1]),
        ("Reject punctuation-only alignment", ["."], [0]),
        ("Reject negative timestamps", list("hi"), [0, -1]),
    ):
        try:
            result = alignment_words(dict(characters=chars, character_start_times_seconds=times), 1)
            record(label, False, f"Accepted: {result!r}")
        except (ValueError, TypeError):
            record(label, True, "Rejected malformed alignment.")

    payload = {"candidates": [{"content": {"parts": ["not an object"]}}]}
    with patch("story_writer.urlopen", side_effect=lambda *a, **k: io.BytesIO(json.dumps(payload).encode())):
        try:
            story_writer.write_with_api("gemini", "fixture", {}, lambda x: False, "fixture")
            record("Handle malformed Gemini parts", False, "Unexpected success.")
        except RuntimeError:
            record("Handle malformed Gemini parts", True, "Provider-level error, eligible for fallback.")
        except Exception as error:
            record("Handle malformed Gemini parts", False, f"Unhandled {type(error).__name__}: {error}")

    for entry in stories.PREMISES.values():
        record(f"Prepared draft format: {entry['id']}",
               story_writer.valid_story(entry["story"]) and story_is_reasonable(entry["story"], 30),
               "Structural validation only; does not certify story quality.")

    if render:
        render_story = {**short,
                        "setup": ["My brother arrived at my workshop with our father, who had promised him a job without asking me.",
                                  "I told my brother he would have to start as an apprentice and earn the same wage as everyone else."],
                        "ending": ["He asked where I kept the work gloves. Our father left, but my brother stayed to finish an order."]}
        for seconds in (10, 30, 44):
            with tempfile.TemporaryDirectory(prefix="framelet-audit-") as folder:
                directory = Path(folder)

                def synthetic_audio(directory, filename, lines, voice, language):
                    script = " ".join(lines)
                    with wave.open(str(directory / filename), "wb") as audio:
                        audio.setparams((1, 2, stories.RATE, 0, "NONE", "not compressed"))
                        audio.writeframes(b"\0\0" * stories.RATE * seconds)
                    words = script.split()
                    return [(0, seconds, script)], [
                        dict(word=word, start=seconds * i / len(words)) for i, word in enumerate(words)
                    ]

                state = {}
                with patch("stories.narrate", side_effect=synthetic_audio), patch("stories.write_story", return_value=render_story):
                    stories.process("render", dict(story=render_story, hook=0, duration=30,
                                    language="en", voice="elevenlabs", background="neon"),
                                    directory, str(app.FFMPEG), os.environ.copy(),
                                    lambda **fields: state.update(fields))
                film = directory / "film.mp4"
                if state.get("status") != "ready":
                    record(f"Reject out-of-range {seconds}s narration without publishing",
                           seconds != 30 and not film.exists() and "duration" in state.get("detail", ""), state)
                    continue
                decoded = subprocess.run([str(app.FFMPEG), "-hide_banner", "-i", str(film),
                                          "-f", "null", "-"], capture_output=True, text=True, timeout=90)
                match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", decoded.stderr)
                duration = (float(match[1]) * 3600 + float(match[2]) * 60 + float(match[3])) if match else None
                record(f"Decode {seconds}s fixture MP4", decoded.returncode == 0, "Synthetic silence; not a voice quality test.")
                record(f"30-second export stays within 10% ({seconds}s input)",
                       duration is not None and 27 <= duration <= 33,
                       dict(actual_seconds=duration, target_seconds=30))
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    rows = audit(args.render)
    path = ROOT / "runtime/platform-audit.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    for row in rows:
        print(f"{'PASS' if row['passed'] else 'GAP '}: {row['check']} — {row['evidence']}")
    print(f"{sum(row['passed'] for row in rows)}/{len(rows)} checks passed. Report: {path}")
    sys.exit(1 if any(not row["passed"] for row in rows) else 0)
