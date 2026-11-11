"""Local story drafting, narration and video export."""
import json
import subprocess
import sys
import wave
from functools import lru_cache
from pathlib import Path

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
DELIVERY = {
    "hook": ("Speak directly to a friend, brisk and curious. Put one clear stress on the strange detail.", 0.20),
    "setup": ("Conversational storytelling. Keep small details moving and voice quoted reactions naturally.", 0.08),
    "tension": ("Build anticipation. Read the ordinary words quickly, then pause at the important clue.", 0.21),
    "reveal": ("Take a beat. Say the surprising words slowly with the emotion they call for, then return to a natural pace.", 0.35),
    "fallout": ("React like a friend telling a story. Let disbelief, hurt, or dry amusement follow the meaning of the words.", 0.14),
    "ending": ("Finish a little more quietly and let the last words land. Keep the reaction true to the text.", 0.08),
}
RUSSIAN_DELIVERY = {
    "hook": "Начни живо, с лёгким удивлением. Выдели голосом одну странную деталь.",
    "setup": "Рассказывай спокойно и связно. Реплики произноси как в обычном разговоре.",
    "tension": "Сохраняй разговорный темп. Слегка замедлись на важной детали.",
    "reveal": "Сделай короткую паузу перед неожиданным словом. Пусть удивление слышится без театральности.",
    "fallout": "Передай реакцию рассказчика голосом, без наигранных эмоций.",
    "ending": "Закончи чуть тише, с естественным снижением голоса.",
}



def ready(language="en"):
    timing_model = TIMING_MODELS[language]
    return all(path.is_file() for path in (
        VOICE_MODEL / "model.safetensors", VOICE_MODEL / "speech_tokenizer/model.safetensors",
        MODELS / "story-writer/model.safetensors", timing_model / "weights.npz",
        timing_model / "config.json", timing_model / "tokenizer.json", timing_model / "preprocessor_config.json",
    ))


@lru_cache(maxsize=1)
def voice_engine():
    from mlx_audio.tts.utils import load_model

    return load_model(VOICE_MODEL)


def narrate(directory, filename, beats, voice, language="en"):
    import numpy as np

    narrator = voice_engine()
    cues = []
    sample_offset = 0
    with wave.open(str(directory / filename), "wb") as audio:
        audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        for line, mood in beats:
            instruction, pause = DELIVERY[mood]
            if language == "ru":
                instruction = ("Рассказывай по-русски, будто делишься историей с другом. "
                               "Произноси только текст, с естественными ударениями. "
                               + RUSSIAN_DELIVERY[mood])
            else:
                instruction += " Read only the text, like telling a friend what happened. Keep the delivery understated."
                if mood in ("tension", "reveal"):
                    pause += 0.16
            chunks = list(narrator.generate_custom_voice(
                text=line, speaker=VOICES[voice], language="russian" if language == "ru" else "english",
                instruct=instruction, max_tokens=480,
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
            silence = round(pause * RATE)
            audio.writeframes(bytes(silence * 2))
            sample_offset += silence
    return cues


def story_beats(story, hook):
    setup, ending = story["setup"], story["ending"]
    lines = [
        (story["hooks"][hook], "hook"),
        *((line, "setup" if index < len(setup) // 2 else "tension") for index, line in enumerate(setup)),
        (ending[0], "reveal"),
        *((line, "fallout") for line in ending[1:-1]),
        (ending[-1], "ending"),
    ]
    beats = []
    for line, mood in lines:
        if beats and beats[-1][1] == mood and len(beats[-1][0]) + 1 + len(line) <= 200:
            beats[-1] = (beats[-1][0] + " " + line, mood)
        else:
            beats.append((line, mood))
    return beats


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
                narrate(directory, f"hook-{index}.wav", [(hook, "hook")], data["voice"], language)
            update(status="pitch", detail="Choose an opening.", story=story)
        elif action == "twist":
            update(status="writing", detail="Writing a different ending…")
            ending = write_story(action, {**data["story"], "duration": data["duration"], "language": language}, env)["ending"]
            update(status="pitch", detail="New ending ready.", story={**data["story"], "ending": ending},
                   video=None)
        else:
            story = data["story"]
            update(status="rendering", detail="Recording the story with expressive narration…")
            cues = narrate(directory, "narration.wav", story_beats(story, data["hook"]), data["voice"], language)
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
    except Exception as error:
        details = getattr(error, "stderr", None) or str(error)
        (directory / "story-error.log").write_text(details)
        update(status="error", detail="The story could not finish. Try again.")
