"""Cloud story drafting and narration; server-side video export."""
import base64
import hashlib
import shutil
import time
import binascii
import json
import os
import re
import ssl
import subprocess
import sys
import wave
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import certifi

from story_captions import FONT_DIR, alignment_words, write_captions
from story_validation import story_is_reasonable
from story_quality import review_story
from story_writer import valid_story, writer_provider

ROOT = Path(__file__).resolve().parent
PREMISES = {
    entry["id"]: entry
    for entry in json.loads((ROOT / "assets/story-premises.json").read_text(encoding="utf-8"))
}
BACKGROUNDS = ROOT / "dist/backgrounds"
CATEGORIES = ("Relationship drama", "Family secrets", "Workplace revenge", "Awkward encounters", "Unexpected kindness")
DURATIONS = (30, 60, 120)
LANGUAGES = {"en": "English", "ru": "Русский"}
VOICES = {"elevenlabs": "ElevenLabs"}
CLIPS = {"neon": "Neon city", "desert": "Desert drive"}
RATE = 24000


def voice_catalog(language):
    default = os.environ.get(f"FRAMELET_ELEVENLABS_VOICE_{language.upper()}", "").strip()
    entries = {"elevenlabs": {"label": "Default narrator", "voice_id": default}} if default else {}
    try:
        configured = json.loads(os.environ.get("FRAMELET_VOICES", "{}"))
        for key, value in configured.get(language, {}).items():
            if (re.fullmatch(r"[a-z0-9_-]{1,40}", key) and isinstance(value, dict)
                    and isinstance(value.get("label"), str) and 0 < len(value["label"]) <= 80
                    and isinstance(value.get("voice_id"), str) and 0 < len(value["voice_id"]) <= 200):
                entries[key] = value
    except (ValueError, TypeError, AttributeError):
        pass
    return entries


def cloud_voice(language, voice="elevenlabs"):
    return voice_catalog(language).get(voice, {}).get("voice_id", "")


def available_voices(language):
    if os.environ.get("ELEVENLABS_API_KEY", "").strip():
        return {key: value["label"] for key, value in voice_catalog(language).items()}
    return {}


def cloud_narration(text, language, voice="elevenlabs"):
    voice_id = quote(cloud_voice(language, voice), safe="")
    request = Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps?output_format=pcm_24000",
        data=json.dumps({
            "text": text,
            "model_id": os.environ.get("FRAMELET_ELEVENLABS_MODEL", "eleven_multilingual_v2"),
            "voice_settings": {"stability": 0.65, "similarity_boost": 0.75, "style": 0},
        }).encode("utf-8"),
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"].strip(), "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=90, context=ssl.create_default_context(cafile=certifi.where())) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise NarrationError(f"ElevenLabs returned HTTP {error.code}. Check the API key, voice ID, and credits.") from None
    except URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise NarrationError("Could not verify the ElevenLabs connection certificate.") from None
        raise NarrationError("Could not reach ElevenLabs. Check your connection and try again.") from None
    except TimeoutError:
        raise NarrationError("ElevenLabs took too long to respond. Please try again.") from None
    except ValueError:
        raise NarrationError("ElevenLabs returned an unreadable response.") from None
    try:
        pcm = base64.b64decode(payload["audio_base64"], validate=True)
        if not pcm or len(pcm) % 2:
            raise ValueError
        alignment = payload.get("alignment") or payload["normalized_alignment"]
        words = alignment_words(alignment, len(pcm) / (2 * RATE))
    except (KeyError, TypeError, ValueError, binascii.Error):
        raise NarrationError("ElevenLabs returned invalid audio or caption timing.") from None
    return pcm, words


class NarrationError(Exception):
    pass


def ready(language="en", voice=None, *, needs_writer=True):
    voices = available_voices(language)
    voice_ready = bool(voices) if voice is None else voice in voices
    provider = writer_provider("draft", os.environ)
    writer_ready = provider in ("groq", "gemini") and bool(os.environ.get(f"{provider.upper()}_API_KEY", "").strip())
    return voice_ready and (writer_ready or not needs_writer)


def narrate(directory, filename, lines, voice, language="en"):
    if voice not in available_voices(language):
        raise NarrationError("Configure ElevenLabs and a voice for the selected language.")
    lines = [line.strip() for line in lines]
    lines = [line if line.rstrip('»"”').endswith((".", "!", "?", "…")) else line + "." for line in lines]
    script = " ".join(lines)
    config = {"text": script, "language": language, "voice": cloud_voice(language, voice),
              "model": os.environ.get("FRAMELET_ELEVENLABS_MODEL", "eleven_multilingual_v2"),
              "settings": {"stability": 0.65, "similarity_boost": 0.75, "style": 0}}
    cache_key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    cache = directory / "audio-cache"
    cache.mkdir(exist_ok=True)
    cached_audio, cached_words = cache / f"{cache_key}.wav", cache / f"{cache_key}.json"
    try:
        with wave.open(str(cached_audio), "rb") as audio:
            if audio.getframerate() != RATE or audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                raise ValueError("Invalid cache")
            pcm = audio.readframes(audio.getnframes())
        words = json.loads(cached_words.read_text())
        if not pcm or not isinstance(words, list) or not words:
            raise ValueError("Invalid cache")
        hit = True
    except (OSError, ValueError, EOFError, wave.Error):
        pcm, words = cloud_narration(script, language, voice)
        hit = False
    with (directory / "usage.jsonl").open("a") as log:
        log.write(json.dumps({"time": time.time(), "service": "elevenlabs", "cache_hit": hit,
                              "characters": 0 if hit else len(script), "model": config["model"]}) + "\n")
    with wave.open(str(directory / filename), "wb") as audio:
        audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        audio.writeframes(pcm)
    if not hit:
        shutil.copyfile(directory / filename, cached_audio.with_suffix(".part.wav"))
        cached_audio.with_suffix(".part.wav").replace(cached_audio)
        cached_words.with_suffix(".part.json").write_text(json.dumps(words))
        cached_words.with_suffix(".part.json").replace(cached_words)
    return [(0, len(pcm) / (2 * RATE), script)], words


def write_story(action, data, env, *, editorial=True):
    try:
        result = subprocess.run(
            [sys.executable, ROOT / "story_writer.py", action], input=json.dumps(data),
            capture_output=True, text=True, check=True, timeout=180, cwd=ROOT, env=env,
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip()
        if detail.startswith(("Groq ", "Gemini ", "Story writer ")) and "\n" not in detail:
            raise NarrationError(detail) from error
        raise NarrationError("The story writer could not finish. Try another idea.") from error
    story = json.loads(result.stdout.splitlines()[-1])
    if not editorial:
        return story
    candidate = {**data, **story} if action == "twist" else story
    review = review_story(candidate, data.get("language", "en"), env, data)
    if review["issues"]:
        original = data.get("story", candidate)
        scope = data.get("scope", "custom") if action == "rewrite" else ("ending" if action == "twist" else "custom")
        repaired = write_story("rewrite", {"story": candidate, "scope": scope,
                               "instruction": "Fix these editorial defects without changing unrelated facts: " + "; ".join(review["issues"]),
                               "duration": data["duration"], "language": data.get("language", "en")}, env, editorial=False)
        # Targeted edits must never drift into other sections.
        section = {"opening": "hooks", "ending": "ending"}.get(scope)
        if section:
            repaired = {**original, section: repaired[section]}
        review = review_story(repaired, data.get("language", "en"), env, data)
        if review["issues"]:
            raise NarrationError("The draft did not pass the story editor. Your previous version is preserved; try a different premise.")
        candidate = repaired
    return {"ending": candidate["ending"]} if action == "twist" else candidate


def process(action, data, directory, ffmpeg, env, update):
    try:
        language = data.get("language", "en")
        if action == "draft":
            update(status="writing", detail="Writing your story and three openings…")
            story = data.get("prepared_story")
            if story is None:
                story = write_story(action, {**{key: data[key] for key in ("category", "idea", "duration")},
                                             "language": language}, env)
            if not story_is_reasonable(story, data["duration"]):
                raise NarrationError("The script does not fit the selected length or repeats itself. Adjust the text and retry.")
            update(status="pitch", detail="Read your story and choose an opening.", story=story)
        elif action == "edit":
            story = {**data["story"], data["section"]: list(data["story"][data["section"]])}
            story[data["section"]][data["index"]] = data["text"]
            if not valid_story(story) or not story_is_reasonable(story, data["duration"]):
                raise NarrationError("This edit repeats a sentence or does not fit the selected duration. Your previous version is saved.")
            update(status="pitch", detail="Sentence saved. The rest of your story is unchanged.", story=story, video=None, preview=None)
        elif action == "twist":
            update(status="writing", detail="Writing a different ending…")
            ending = write_story(action, {**data["story"], "duration": data["duration"], "language": language}, env)["ending"]
            update(status="pitch", detail="New ending ready.", story={**data["story"], "ending": ending},
                   video=None)
        elif action == "rewrite":
            update(status="writing", detail="Rewriting your story…")
            story = write_story(action, {
                "story": data["story"], "scope": data["scope"], "instruction": data["instruction"],
                "duration": data["duration"], "language": language,
            }, env)
            if not valid_story(story) or not story_is_reasonable(story, data["duration"]):
                raise NarrationError("The revision did not fit the selected length. Try a smaller edit.")
            update(status="pitch", detail="Revision ready to read.", story=story, video=None, preview=None)
        elif action == "preview":
            hook = data["hook"]
            update(status="writing", detail="Recording the selected opening…")
            narrate(directory, "hook.part.wav", [data["story"]["hooks"][hook]], data["voice"], language)
            (directory / "hook.part.wav").replace(directory / f"hook-{hook}.wav")
            update(status="pitch", detail="Opening preview ready.", preview=f"/hooks/{directory.name}/hook-{hook}.wav")
        else:
            story = data["story"]
            if not story_is_reasonable(story, data["duration"]):
                raise NarrationError("The script does not fit the selected length or repeats itself. Adjust the text and retry.")
            update(status="rendering", detail="Recording the narration…")
            cues, words = narrate(directory, "narration.wav", [story["hooks"][data["hook"]], *story["setup"], *story["ending"]], data["voice"], language)
            update(status="rendering", detail="Timing captions to the narration…")
            with wave.open(str(directory / "narration.wav")) as narration:
                spoken_duration = narration.getnframes() / RATE
            ratio = spoken_duration / data["duration"]
            if not 0.90 <= ratio <= 1.10:
                # One bounded repair, never clip speech or force an unnatural playback speed.
                update(detail="Adjusting the script to the requested speaking time…")
                target_words = round(len(cues[0][2].split()) / ratio)
                adjusted = write_story("rewrite", {"story": story, "scope": "custom", "language": language,
                                       "duration": data["duration"], "instruction":
                                       f"Fit one opening plus the body to about {target_words} words. Preserve the events, motives and payoff. "
                                       "Use useful concrete actions, never padding. Keep all three openings compatible."}, env)
                if not valid_story(adjusted) or not story_is_reasonable(adjusted, data["duration"]):
                    raise NarrationError("The timing correction failed. Your previous script is preserved.")
                cues, words = narrate(directory, "narration.wav", [adjusted["hooks"][data["hook"]], *adjusted["setup"], *adjusted["ending"]], data["voice"], language)
                spoken_duration = cues[0][1]
                ratio = spoken_duration / data["duration"]
                if not 0.90 <= ratio <= 1.10:
                    raise NarrationError("The voice could not fit this duration naturally. Edit the script or choose another target length.")
                story = adjusted
                update(story=story, detail="Script adjusted to fit the requested time.")
            tempo = ratio
            write_captions(directory, cues, words, tempo, language, data.get("caption_style", "bold"))
            update(status="rendering", detail="Exporting your short…")
            with (directory / "story-render.log").open("w") as log:
                subprocess.run([
                    ffmpeg, "-y", "-stream_loop", "-1", "-i", BACKGROUNDS / f"{data['background']}.mp4",
                    "-i", "narration.wav", "-vf", f"ass=story.ass:fontsdir='{FONT_DIR}'",
                    "-map", "0:v:0", "-map", "1:a:0", "-af", f"atempo={tempo:.8f},loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-t", f"{spoken_duration / tempo + 0.1:.3f}", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-movflags", "+faststart", "story.part.mp4",
                ], cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
            (directory / "story.part.mp4").replace(directory / "film.mp4")
            update(status="ready", detail="Your story is ready.", video=f"/films/{directory.name}/film.mp4", actual_duration=round(spoken_duration / tempo, 2))
    except NarrationError as error:
        update(status="error", detail=str(error))
    except Exception as error:
        details = getattr(error, "stderr", None) or str(error)
        (directory / "story-error.log").write_text(details)
        update(status="error", detail="The story could not finish. Try again.")
