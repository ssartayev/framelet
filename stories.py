"""Local story drafting, narration and video export."""
import json
import os
import ssl
import subprocess
import sys
import wave
from functools import lru_cache
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import certifi

from story_captions import FONT_DIR, TIMING_MODELS, write_captions

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "runtime/models"
VOICE_MODEL = MODELS / "narrator"
BACKGROUNDS = ROOT / "dist/backgrounds"
CATEGORIES = ("Relationship drama", "Family secrets", "Workplace revenge", "Awkward encounters", "Unexpected kindness")
DURATIONS = (30, 60, 120)
LANGUAGES = {"en": "English", "ru": "Русский"}
VOICES = {"af_heart": "Vivian", "am_michael": "Aiden", "bf_emma": "Ryan"}
CLIPS = {"neon": "Neon city", "desert": "Desert drive"}
RATE = 24000
LOCAL_STYLE = {
    "en": "Read in a calm, conversational voice, as if telling a friend what happened.",
    "ru": "Рассказывай спокойно и естественно, как в разговоре с другом.",
}


def cloud_voice(language):
    return os.environ.get(f"FRAMELET_ELEVENLABS_VOICE_{language.upper()}", "").strip()


def available_voices(language):
    voices = {}
    if os.environ.get("ELEVENLABS_API_KEY", "").strip() and cloud_voice(language):
        voices["elevenlabs"] = "ElevenLabs"
    if all((VOICE_MODEL / name).is_file() for name in (
        "model.safetensors", "speech_tokenizer/model.safetensors",
    )):
        voices.update(VOICES)
    return voices


def cloud_narration(text, language):
    voice_id = quote(cloud_voice(language), safe="")
    request = Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=pcm_24000",
        data=json.dumps({
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.65, "similarity_boost": 0.75, "style": 0},
        }).encode("utf-8"),
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"].strip(), "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=90, context=ssl.create_default_context(cafile=certifi.where())) as response:
            pcm = response.read()
    except HTTPError as error:
        raise NarrationError(f"ElevenLabs returned HTTP {error.code}. Check the API key, voice ID, and credits.") from None
    except URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise NarrationError("Could not verify the ElevenLabs connection certificate.") from None
        raise NarrationError("Could not reach ElevenLabs. Check your connection and try again.") from None
    except TimeoutError:
        raise NarrationError("ElevenLabs took too long to respond. Please try again.") from None
    if not pcm or len(pcm) % 2:
        raise NarrationError("ElevenLabs returned invalid audio.")
    return pcm


class NarrationError(Exception):
    pass


def ready(language="en", voice=None):
    timing_model = TIMING_MODELS[language]
    voices = available_voices(language)
    voice_ready = bool(voices) if voice is None else voice in voices
    return voice_ready and all(path.is_file() for path in (
        MODELS / "story-writer/model.safetensors", timing_model / "weights.npz",
        timing_model / "config.json", timing_model / "tokenizer.json", timing_model / "preprocessor_config.json",
    ))


@lru_cache(maxsize=1)
def voice_engine():
    from mlx_audio.tts.utils import load_model

    return load_model(VOICE_MODEL)


def narrate(directory, filename, lines, voice, language="en"):
    if voice == "elevenlabs":
        script = " ".join(lines)
        pcm = cloud_narration(script, language)
        with wave.open(str(directory / filename), "wb") as audio:
            audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            audio.writeframes(pcm)
        return [(0, len(pcm) / (2 * RATE), script)]

    import numpy as np

    narrator = voice_engine()
    cues = []
    sample_offset = 0
    with wave.open(str(directory / filename), "wb") as audio:
        audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        for line in narration_chunks(lines):
            chunks = list(narrator.generate_custom_voice(
                text=line, speaker=VOICES[voice], language="russian" if language == "ru" else "english",
                instruct=LOCAL_STYLE[language], temperature=0.65, max_tokens=420,
            ))
            if not chunks or any(chunk.sample_rate != RATE for chunk in chunks):
                raise ValueError("Unexpected narration audio")
            samples = np.concatenate([np.asarray(chunk.audio).reshape(-1) for chunk in chunks])
            audible = np.flatnonzero(np.abs(samples) > max(0.002, float(np.max(np.abs(samples))) * 0.015))
            if not audible.size:
                raise ValueError("The narrator produced silent audio")
            samples = samples[max(0, audible[0] - 720):audible[-1] + 2160]
            pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
            audio.writeframes(pcm.tobytes())
            cues.append((sample_offset / RATE, (sample_offset + len(pcm)) / RATE, line))
            sample_offset += len(pcm)
            silence = round(0.12 * RATE)
            audio.writeframes(bytes(silence * 2))
            sample_offset += silence
    return cues


def narration_chunks(lines):
    paragraph = ""
    for line in lines:
        if paragraph and len(paragraph) + 1 + len(line) > 170:
            yield paragraph
            paragraph = ""
        paragraph = f"{paragraph} {line}" if paragraph else line
    if paragraph:
        yield paragraph


def write_story(action, data, env):
    result = subprocess.run(
        [sys.executable, ROOT / "story_writer.py", action], input=json.dumps(data),
        capture_output=True, text=True, check=True, timeout=180, cwd=ROOT, env=env,
    )
    return json.loads(result.stdout.splitlines()[-1])


def process(action, data, directory, ffmpeg, env, update):
    try:
        language = data.get("language", "en")
        if action == "draft":
            update(status="writing", detail="Writing your story and three openings…")
            story = write_story(action, {**{key: data[key] for key in ("category", "idea", "duration")},
                                         "language": language}, env)
            update(status="writing", detail="Recording your hook previews…")
            for index, hook in enumerate(story["hooks"]):
                narrate(directory, f"hook-{index}.wav", [hook], data["voice"], language)
            update(status="pitch", detail="Choose an opening.", story=story)
        elif action == "twist":
            update(status="writing", detail="Writing a different ending…")
            ending = write_story(action, {**data["story"], "duration": data["duration"], "language": language}, env)["ending"]
            update(status="pitch", detail="New ending ready.", story={**data["story"], "ending": ending},
                   video=None)
        else:
            story = data["story"]
            update(status="rendering", detail="Recording the narration…")
            cues = narrate(directory, "narration.wav", [story["hooks"][data["hook"]], *story["setup"], *story["ending"]], data["voice"], language)
            update(status="rendering", detail="Timing captions to the narration…")
            with wave.open(str(directory / "narration.wav")) as narration:
                spoken_duration = narration.getnframes() / RATE
            tempo = min(1.2, max(0.85, spoken_duration / data["duration"]))
            write_captions(directory, cues, tempo, language)
            update(status="rendering", detail="Exporting your short…")
            with (directory / "story-render.log").open("w") as log:
                subprocess.run([
                    ffmpeg, "-y", "-stream_loop", "-1", "-i", BACKGROUNDS / f"{data['background']}.mp4",
                    "-i", "narration.wav", "-vf", f"ass=story.ass:fontsdir='{FONT_DIR}'",
                    "-map", "0:v:0", "-map", "1:a:0", "-af", f"atempo={tempo:.8f}", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", "story.part.mp4",
                ], cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
            (directory / "story.part.mp4").replace(directory / "film.mp4")
            update(status="ready", detail="Your story is ready.", video=f"/films/{directory.name}/film.mp4")
    except NarrationError as error:
        update(status="error", detail=str(error))
    except Exception as error:
        details = getattr(error, "stderr", None) or str(error)
        (directory / "story-error.log").write_text(details)
        update(status="error", detail="The story could not finish. Try again.")
