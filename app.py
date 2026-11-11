import argparse
import json
import os
import random
import re
import subprocess
import sys
import textwrap
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import stories

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
MODELS = RUNTIME / "models"
BIN = RUNTIME / "bin"
FFMPEG = BIN / "ffmpeg"
VIDEO_MODEL = RUNTIME / "wan"
ENGINE = VIDEO_MODEL / "bin/sd-cli"
OUTPUT = RUNTIME / "renders"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FRAMES = 17
SOURCE_FPS = 8
FPS = 24
LENGTH = 2
WIDTH, HEIGHT = 192, 336
EXPORT_WIDTH, EXPORT_HEIGHT = 360, 640
JOBS = {}
LOCK = threading.Lock()
WORKER = ThreadPoolExecutor(max_workers=1)
ENV = {**os.environ, "PATH": f"{BIN}:{os.environ['PATH']}", "HF_HUB_OFFLINE": "1"}
SURPRISES = (
    "a quiet robot in an abandoned city", "a strange discovery under the ocean",
    "a tiny creature inside a clock", "a plant with a peculiar ability",
    "an impossible doorway in the snow", "an unusual delivery to a space station",
)
VIDEO_WEIGHTS = ("wan.gguf", "t5.gguf", "tae.safetensors")


def model_status():
    return {
        "writer": (MODELS / "writer/model.safetensors").is_file(),
        "video": ENGINE.is_file() and all((VIDEO_MODEL / item).is_file() for item in VIDEO_WEIGHTS),
    }


def update(job_id, **fields):
    with LOCK:
        JOBS[job_id].update(fields)


def run(args, directory, log):
    subprocess.run(args, cwd=directory, env=ENV, stdout=log, stderr=subprocess.STDOUT,
                   check=True, timeout=1800)


def write_pitch(job_id, idea):
    directory = OUTPUT / job_id
    try:
        update(job_id, status="writing", detail="Writing a three-shot pitch…")
        prompt = idea or f"Surprise me with a fictional micro-story about {random.choice(SURPRISES)}."
        result = subprocess.run([sys.executable, ROOT / "writer.py", prompt], cwd=ROOT, env=ENV,
                                capture_output=True, text=True, check=True, timeout=180)
        plan = json.loads(result.stdout.splitlines()[-1])
        update(job_id, status="pitch", detail="Pitch ready", plan=plan)
    except Exception as error:
        if isinstance(error, subprocess.CalledProcessError):
            (directory / "writer.log").write_text(error.stderr or "")
        else:
            (directory / "writer.log").write_text(str(error))
        update(job_id, status="error", detail="The local writer couldn't make a pitch. Try again with a short idea.")


def render(job_id, plan):
    directory = OUTPUT / job_id
    phase = "video model"
    with (directory / "render.log").open("w") as log:
        try:
            for index, shot in enumerate(plan["shots"], start=1):
                phase = f"shot {index}"
                update(job_id, status="rendering", detail=f"Generating moving footage · shot {index} of 3",
                       progress=10 + (index - 1) * 26)
                source = f"raw-{index}.avi"
                run([ENGINE, "-M", "vid_gen", "--prompt", shot["visual"], "--output", source,
                     "--diffusion-model", VIDEO_MODEL / "wan.gguf", "--t5xxl", VIDEO_MODEL / "t5.gguf",
                     "--tae", VIDEO_MODEL / "tae.safetensors", "--sampling-method", "euler",
                     "--steps", "10", "--cfg-scale", "4", "--flow-shift", "3",
                     "-H", str(HEIGHT), "-W", str(WIDTH), "--video-frames", str(FRAMES),
                     "--fps", str(SOURCE_FPS)], directory, log)
                voice = f"voice-{index}.aiff"
                spoken = shot["voice"].strip()
                voice_text = directory / f"voice-{index}.txt"
                voice_text.write_text(spoken)
                run(["/usr/bin/say", "-v", "Samantha", "-r", "225", "-o", voice, "-f", voice_text],
                    directory, log)
                (directory / "caption.txt").write_text(wrap_caption(spoken))
                vf = (
                    f"scale={EXPORT_WIDTH}:{EXPORT_HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={EXPORT_WIDTH}:{EXPORT_HEIGHT},"
                    f"fps={FPS},setsar=1,drawtext=fontfile='{FONT}':textfile=caption.txt:"
                    "expansion=none:fontsize=25:fontcolor=white:borderw=2:bordercolor=black:"
                    "line_spacing=5:x=(w-tw)/2:y=h-th-66"
                )
                run([FFMPEG, "-y", "-i", source, "-i", voice, "-vf", vf, "-af", "apad",
                     "-map", "0:v:0", "-map", "1:a:0", "-t", str(LENGTH), "-c:v", "libx264",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "1",
                     f"shot-{index}.mp4"], directory, log)

            phase = "final export"
            update(job_id, detail="Joining shots and exporting the MP4…", progress=92)
            (directory / "concat.txt").write_text("".join(f"file 'shot-{i}.mp4'\n" for i in range(1, 4)))
            run([FFMPEG, "-y", "-f", "concat", "-safe", "1", "-i", "concat.txt",
                 "-c:v", "copy", "-af", "asetpts=N/SR/TB", "-c:a", "aac",
                 "-t", str(LENGTH * len(plan["shots"])), "-movflags", "+faststart", "film.mp4"],
                directory, log)
            update(job_id, status="ready", detail="Your film is ready", progress=100,
                   video=f"/films/{job_id}/film.mp4")
        except Exception as error:
            print(error, file=log)
            update(job_id, status="error", detail=f"The local render failed during {phase}. "
                   f"See runtime/renders/{job_id}/render.log for details.")


def wrap_caption(text):
    return "\n".join(textwrap.wrap(text, width=20))


class App(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "dist"), **kwargs)

    def send_json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def serve_media(self, video, content_type, head_only=False):
        if not video.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        size = video.stat().st_size
        start, end = 0, size - 1
        header = self.headers.get("Range")
        if header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
            if match and match[1]:
                start = int(match[1])
                end = min(end, int(match[2])) if match[2] else end
            elif match and match[2]:
                start = max(0, size - int(match[2]))
            if not match or not any(match.groups()) or start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT if header else HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not head_only:
            with video.open("rb") as stream:
                stream.seek(start)
                while length:
                    data = stream.read(min(length, 65536))
                    if not data:
                        break
                    self.wfile.write(data)
                    length -= len(data)

    def serve_render_asset(self, path, head_only=False):
        film = re.fullmatch(r"/films/([a-f0-9]{32})/film\.mp4", path)
        if film:
            self.serve_media(OUTPUT / film[1] / "film.mp4", "video/mp4", head_only)
            return True
        hook = re.fullmatch(r"/hooks/([a-f0-9]{32})/(hook-[0-2]\.wav)", path)
        if hook:
            self.serve_media(OUTPUT / hook[1] / hook[2], "audio/wav", head_only)
            return True
        return False

    def do_HEAD(self):
        if not self.serve_render_asset(urlsplit(self.path).path, head_only=True):
            super().do_HEAD()

    def do_GET(self):
        path = urlsplit(self.path).path
        if self.serve_render_asset(path):
            return
        if path == "/api/status":
            self.send_json(HTTPStatus.OK, model_status())
        elif path == "/api/stories/options":
            self.send_json(HTTPStatus.OK, {"voices": stories.VOICES, "categories": stories.CATEGORIES,
                                          "backgrounds": stories.CLIPS, "ready": stories.ready() and model_status()["writer"]})
        elif path.startswith("/api/jobs/"):
            job_id = path.removeprefix("/api/jobs/")
            with LOCK:
                job = JOBS.get(job_id, {}).copy()
            self.send_json(HTTPStatus.OK if job else HTTPStatus.NOT_FOUND,
                           job or {"error": "Job not found."})
        else:
            super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
            return self.send_json(HTTPStatus.FORBIDDEN, {"error": "Use the local studio page."})
        if path == "/api/pitch":
            return self.create_pitch()
        if path == "/api/stories":
            return self.create_story()
        story_action = re.fullmatch(r"/api/stories/([a-f0-9]{32})/(twist|render)", path)
        if story_action:
            return self.change_story(*story_action.groups())
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/render", path)
        if not match:
            return self.send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found."})
        if not model_status()["video"]:
            return self.send_json(HTTPStatus.SERVICE_UNAVAILABLE,
                                  {"error": "The video engine is still downloading. This pitch is saved for retry."})

        job_id = match[1]
        with LOCK:
            job = JOBS.get(job_id)
            if not job or job.get("kind") == "story" or job["status"] not in ("pitch", "error") or "plan" not in job:
                plan = None
            else:
                plan = job["plan"]
                job.update(status="queued", detail="Waiting for the local video engine…", progress=5)
        if plan is None:
            return self.send_json(HTTPStatus.CONFLICT, {"error": "Create a fresh pitch before rendering."})
        WORKER.submit(render, job_id, plan)
        self.send_json(HTTPStatus.ACCEPTED, {"id": job_id})

    def read_body(self):
        size = int(self.headers.get("Content-Length", 0))
        if not 0 < size <= 8192:
            raise ValueError
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise ValueError
        return data

    def submit_story(self, action, job_id, data):
        WORKER.submit(stories.process, action, data, OUTPUT / job_id, FFMPEG, ENV,
                      lambda **fields: update(job_id, **fields))
        self.send_json(HTTPStatus.ACCEPTED, {"id": job_id})

    def create_story(self):
        try:
            data = self.read_body()
            idea, category, voice = data.get("idea", ""), data["category"], data["voice"]
            duration = data.get("duration", 60)
            if type(duration) is not int or duration not in stories.DURATIONS:
                raise ValueError
            if not isinstance(idea, str) or len(idea) > 500 or category not in stories.CATEGORIES or voice not in stories.VOICES:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Select a genre, narrator, and length of 30, 60, or 120 seconds."})
        if not stories.ready() or not model_status()["writer"]:
            return self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Finish local story setup first."})
        job_id = uuid.uuid4().hex
        (OUTPUT / job_id).mkdir()
        with LOCK:
            JOBS[job_id] = {"kind": "story", "voice": voice, "duration": duration,
                            "status": "queued", "detail": "Story queued…"}
        self.submit_story("draft", job_id, {"idea": idea.strip(), "category": category,
                                           "voice": voice, "duration": duration})

    def change_story(self, job_id, action):
        data = {}
        if action == "render":
            try:
                data = self.read_body()
                hook, background = data["hook"], data["background"]
                if type(hook) is not int or not 0 <= hook < 3 or background not in stories.CLIPS:
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Choose a valid opening and background."})
        with LOCK:
            job = JOBS.get(job_id)
            if not job or job.get("kind") != "story" or "story" not in job or job["status"] not in ("pitch", "ready", "error"):
                data = None
            else:
                data = {**data, "story": job["story"], "voice": job["voice"], "duration": job.get("duration", 60)}
                job.update(status="queued", detail="Story queued…")
        if data is None:
            return self.send_json(HTTPStatus.CONFLICT, {"error": "Write a story or wait for the current task to finish."})
        self.submit_story(action, job_id, data)

    def create_pitch(self):
        try:
            idea = self.read_body()["idea"]
            if not isinstance(idea, str) or len(idea) > 500:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Enter an idea of up to 500 characters."})
        if not model_status()["writer"]:
            return self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "The local writer weights are still being set up."})
        job_id = uuid.uuid4().hex
        (OUTPUT / job_id).mkdir()
        with LOCK:
            JOBS[job_id] = {"status": "queued", "detail": "Waiting for the local writer…"}
        WORKER.submit(write_pitch, job_id, idea.strip())
        self.send_json(HTTPStatus.ACCEPTED, {"id": job_id})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    options = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    print(f"Framelet Studio · http://127.0.0.1:{options.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", options.port), App).serve_forever()
