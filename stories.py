"""Local story drafting, narration and video export."""
import base64
import binascii
import json
import os
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


def cloud_voice(language):
    return os.environ.get(f"FRAMELET_ELEVENLABS_VOICE_{language.upper()}", "").strip()


def available_voices(language):
    if os.environ.get("ELEVENLABS_API_KEY", "").strip() and cloud_voice(language):
        return VOICES.copy()
    return {}


def cloud_narration(text, language):
    voice_id = quote(cloud_voice(language), safe="")
    request = Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps?output_format=pcm_24000",
        data=json.dumps({
            "text": text,
            "model_id": "eleven_multilingual_v2",
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
    pcm, words = cloud_narration(script, language)
    with wave.open(str(directory / filename), "wb") as audio:
        audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        audio.writeframes(pcm)
    return [(0, len(pcm) / (2 * RATE), script)], words


def write_story(action, data, env):
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
    return json.loads(result.stdout.splitlines()[-1])


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
                raise NarrationError("The story was too long or repetitive. Please generate a new one.")
            update(status="pitch", detail="Read your story and choose an opening.", story=story)
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
                raise NarrationError("The story was too long or repetitive. Please generate a new one.")
            update(status="rendering", detail="Recording the narration…")
            cues, words = narrate(directory, "narration.wav", [story["hooks"][data["hook"]], *story["setup"], *story["ending"]], data["voice"], language)
            update(status="rendering", detail="Timing captions to the narration…")
            with wave.open(str(directory / "narration.wav")) as narration:
                spoken_duration = narration.getnframes() / RATE
            if spoken_duration > data["duration"] * 1.5:
                raise NarrationError("The narration is too long for the selected duration. Please generate a new story.")
            tempo = min(1.2, max(0.85, spoken_duration / data["duration"]))
            write_captions(directory, cues, words, tempo, language)
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
            update(status="ready", detail="Your story is ready.", video=f"/films/{directory.name}/film.mp4")
    except NarrationError as error:
        update(status="error", detail=str(error))
    except Exception as error:
        details = getattr(error, "stderr", None) or str(error)
        (directory / "story-error.log").write_text(details)
        update(status="error", detail="The story could not finish. Try again.")
