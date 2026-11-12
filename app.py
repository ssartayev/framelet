import argparse
import base64
import copy
import sqlite3
import hashlib
import hmac
import secrets
import time
from http.cookies import SimpleCookie
from job_store import JobStore
import json
import os
import random
import re
import shutil
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

if __name__ == "__main__":
    from env_config import load_env
    load_env()

import stories

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
MODELS = RUNTIME / "models"
BIN = RUNTIME / "bin"
FFMPEG = os.environ.get("FRAMELET_FFMPEG") or shutil.which("ffmpeg") or BIN / "ffmpeg"
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
WORKER = ThreadPoolExecutor(max_workers=2)
RENDER_WORKER = ThreadPoolExecutor(max_workers=1)
POST_LOCK = threading.Lock()
ACTIVE = ("queued", "writing", "rendering")
MAX_PENDING = 8
MAX_OWNER_PENDING = 2
ENV = {**os.environ, "PATH": f"{BIN}:{os.environ['PATH']}", "HF_HUB_OFFLINE": "1"}
SURPRISES = (
    "a quiet robot in an abandoned city", "a strange discovery under the ocean",
    "a tiny creature inside a clock", "a plant with a peculiar ability",
    "an impossible doorway in the snow", "an unusual delivery to a space station",
)
VIDEO_WEIGHTS = ("wan.gguf", "t5.gguf", "tae.safetensors")


class SubmissionError(Exception):
    pass


def model_status():
    return {
        "writer": (MODELS / "writer/model.safetensors").is_file(),
        "video": ENGINE.is_file() and all((VIDEO_MODEL / item).is_file() for item in VIDEO_WEIGHTS),
    }


def store():
    return JobStore(OUTPUT / "jobs.sqlite3")


def update(job_id, **fields):
    with LOCK:
        job = JOBS[job_id]
        if "story" in fields and job.get("story") and fields["story"] != job["story"]:
            job["versions"] = [*job.get("versions", []), {"story": job["story"], "at": time.time()}][-20:]
        JOBS[job_id].update(fields, updated_at=time.time())
        store().save(job_id, JOBS[job_id])


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
    stories_only = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "dist"), **kwargs)

    def authorize(self):
        if os.environ.get("FRAMELET_HTTPS_PROXY") == "1" and self.headers.get("X-Forwarded-Proto") != "https":
            self.send_error(403, "HTTPS ingress required")
            return False
        token = os.environ.get("FRAMELET_ACCESS_TOKEN", "")
        if token:
            try:
                scheme, encoded = self.headers.get("Authorization", "").split(" ", 1)
                supplied = base64.b64decode(encoded, validate=True).decode().split(":", 1)[1]
                allowed = scheme.lower() == "basic" and hmac.compare_digest(supplied.encode(), token.encode())
            except (ValueError, IndexError, UnicodeError):
                allowed = False
            if not allowed:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Framelet beta", charset="UTF-8"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        value = cookie.get("framelet_session")
        value = value.value if value else ""
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            value = secrets.token_hex(32)
            self.new_session = value
        self.owner = hashlib.sha256(value.encode()).hexdigest()
        return True

    def end_headers(self):
        if getattr(self, "new_session", None):
            secure = "; Secure" if (os.environ.get("FRAMELET_SECURE_COOKIES") == "1" or os.environ.get("FRAMELET_HTTPS_PROXY") == "1") else ""
            self.send_header("Set-Cookie", f"framelet_session={self.new_session}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000{secure}")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def owns_unlocked(self, job_id):
        return JOBS.get(job_id, {}).get("owner") == self.owner

    def owns(self, job_id):
        with LOCK:
            return JOBS.get(job_id, {}).get("owner") == self.owner

    def save_submission(self, job_id):
        with LOCK:
            JOBS[job_id]["updated_at"] = time.time()
            try:
                store().save(job_id, JOBS[job_id], getattr(self, "idempotency", None))
            except (OSError, sqlite3.Error) as error:
                original = getattr(self, "original_job", None)
                if original is None:
                    JOBS.pop(job_id, None)
                    try:
                        (OUTPUT / job_id).rmdir()
                    except OSError:
                        pass
                else:
                    JOBS[job_id] = original
                raise SubmissionError("Could not save this action. No generation was started; try again later.") from error

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
            if not self.owns(film[1]):
                self.send_error(404)
                return True
            self.serve_media(OUTPUT / film[1] / "film.mp4", "video/mp4", head_only)
            return True
        hook = re.fullmatch(r"/hooks/([a-f0-9]{32})/(hook-[0-2]\.wav)", path)
        if hook:
            if not self.owns(hook[1]):
                self.send_error(404)
                return True
            self.serve_media(OUTPUT / hook[1] / hook[2], "audio/wav", head_only)
            return True
        return False

    def do_HEAD(self):
        if not self.authorize():
            return
        if not self.serve_render_asset(urlsplit(self.path).path, head_only=True):
            super().do_HEAD()

    def do_GET(self):
        if not self.authorize():
            return
        path = urlsplit(self.path).path
        if self.serve_render_asset(path):
            return
        if self.stories_only and path in ("/", "/index.html"):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/stories.html")
            self.end_headers()
        elif path == "/api/status":
            self.send_json(HTTPStatus.OK, {"writer": False, "video": False} if self.stories_only else model_status())
        elif path == "/api/stories/options":
            ready_by_language = {
                language: stories.ready(language) for language in stories.LANGUAGES
            }
            self.send_json(HTTPStatus.OK, {
                "voices": stories.VOICES, "categories": stories.CATEGORIES,
                "storiesOnly": self.stories_only,
                "backgrounds": stories.CLIPS, "languages": stories.LANGUAGES,
                "voicesByLanguage": {language: stories.available_voices(language)
                                     for language in stories.LANGUAGES},
                "readyByLanguage": ready_by_language, "ready": ready_by_language["en"],
                "preparedStoryReady": stories.ready("ru", needs_writer=False),
                "premises": [
                    {field: entry[field] for field in ("id", "title", "idea", "category", "source")}
                    for entry in stories.PREMISES.values()
                ],
            })
        elif path == "/api/projects":
            with LOCK:
                projects = [{"id": key, "title": job.get("story", {}).get("title", "Untitled"),
                             "status": job["status"], "updated_at": job.get("updated_at", 0)}
                            for key, job in JOBS.items() if job.get("owner") == self.owner]
            self.send_json(HTTPStatus.OK, {"projects": sorted(projects, key=lambda j: j["updated_at"], reverse=True)[:50]})
        elif path.startswith("/api/jobs/"):
            job_id = path.removeprefix("/api/jobs/")
            with LOCK:
                job = JOBS.get(job_id, {}).copy() if self.owns_unlocked(job_id) else {}
            self.send_json(HTTPStatus.OK if job else HTTPStatus.NOT_FOUND,
                           {key: value for key, value in job.items() if key != "owner"} if job else {"error": "Job not found."})
        else:
            super().do_GET()

    def do_POST(self):
        if not self.authorize():
            return
        host = self.headers.get("Host")
        if self.headers.get("Origin") not in (None, f"http://{host}", f"https://{host}"):
            return self.send_json(403, {"error": "Use the Framelet studio page."})
        try:
            self.payload = self.read_body(limit=25_000)
        except (ValueError, TypeError):
            return self.send_json(400, {"error": "Send a JSON object of at most 25 KB."})
        key = self.headers.get("Idempotency-Key")
        if key and not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
            return self.send_json(400, {"error": "Invalid request identifier."})
        digest = hashlib.sha256((self.path + json.dumps(self.payload, sort_keys=True)).encode()).hexdigest()
        with POST_LOCK:
            if key:
                previous = store().response(self.owner, key)
                if previous:
                    if previous[0] != digest:
                        return self.send_json(409, {"error": "Request identifier was already used for a different action."})
                    return self.send_json(202, previous[1])
                self.idempotency = (self.owner, key, digest)
            with LOCK:
                pending = [job for job in JOBS.values() if job.get("status") in ACTIVE]
            if len(pending) >= MAX_PENDING or sum(job.get("owner") == self.owner for job in pending) >= MAX_OWNER_PENDING:
                return self.send_json(429, {"error": "Your studio is busy. Wait for a current job to finish."})
            try:
                self.dispatch_post()
            except SubmissionError as error:
                self.send_json(503, {"error": str(error)})

    def dispatch_post(self):
        path = urlsplit(self.path).path
        host = self.headers.get("Host")
        if self.headers.get("Origin") not in (None, f"http://{host}", f"https://{host}"):
            return self.send_json(HTTPStatus.FORBIDDEN, {"error": "Use the Framelet studio page."})
        if path == "/api/pitch" and not self.stories_only:
            return self.create_pitch()
        if path == "/api/stories":
            return self.create_story()
        story_action = re.fullmatch(r"/api/stories/([a-f0-9]{32})/(twist|rewrite|preview|render|edit)", path)
        if story_action:
            return self.change_story(*story_action.groups())
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/render", path)
        if not match or self.stories_only:
            return self.send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found."})
        if not self.owns(match[1]):
            return self.send_json(404, {"error": "Job not found."})
        if not model_status()["video"]:
            return self.send_json(HTTPStatus.SERVICE_UNAVAILABLE,
                                  {"error": "The video engine is still downloading. This pitch is saved for retry."})

        job_id = match[1]
        with LOCK:
            job = JOBS.get(job_id)
            if not job or job.get("kind") == "story" or job["status"] not in ("pitch", "error") or "plan" not in job:
                plan = None
            else:
                self.original_job = copy.deepcopy(job)
                plan = job["plan"]
                job.update(status="queued", detail="Waiting for the local video engine…", progress=5)
        if plan is None:
            return self.send_json(HTTPStatus.CONFLICT, {"error": "Create a fresh pitch before rendering."})
        self.save_submission(job_id)
        RENDER_WORKER.submit(render, job_id, plan)
        self.send_json(HTTPStatus.ACCEPTED, {"id": job_id})

    def read_body(self, limit=8192):
        if hasattr(self, "payload"):
            return self.payload
        self.connection.settimeout(15)
        size = int(self.headers.get("Content-Length", 0))
        if not 0 < size <= limit:
            raise ValueError
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise ValueError
        return data

    def submit_story(self, action, job_id, data):
        self.save_submission(job_id)
        executor = RENDER_WORKER if action == "render" else WORKER
        executor.submit(stories.process, action, data, OUTPUT / job_id, FFMPEG, ENV,
                      lambda **fields: update(job_id, **fields))
        self.send_json(HTTPStatus.ACCEPTED, {"id": job_id})

    def create_story(self):
        try:
            data = self.read_body(limit=25_000)
            idea, category, voice = data.get("idea", ""), data["category"], data["voice"]
            duration = data.get("duration", 60)
            language = data.get("language", "en")
            if not isinstance(language, str) or language not in stories.LANGUAGES:
                return self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Choose a supported story language."})
            if type(duration) is not int or duration not in stories.DURATIONS:
                raise ValueError
            prepared_story = None
            if "premise" in data:
                entry = stories.PREMISES.get(data["premise"])
                if entry is None or language != "ru" or duration != 30:
                    return self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Choose a prepared 30-second Russian story."})
                prepared_story = entry["story"]
            if (not isinstance(idea, str) or len(idea) > 4000
                    or category not in stories.CATEGORIES
                    or voice not in stories.available_voices(language)):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Select a genre, narrator, and length of 30, 60, or 120 seconds."})
        if not stories.ready(language, voice, needs_writer=prepared_story is None):
            return self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Configure a cloud writer key and an ElevenLabs voice for this language."})
        job_id = uuid.uuid4().hex
        (OUTPUT / job_id).mkdir()
        with LOCK:
            JOBS[job_id] = {"owner": self.owner, "kind": "story", "voice": voice, "duration": duration, "language": language,
                            "status": "queued", "detail": "Story queued…"}
        self.submit_story("draft", job_id, {"idea": idea.strip(), "category": category,
                                           "voice": voice, "duration": duration, "language": language,
                                           "prepared_story": prepared_story})

    def change_story(self, job_id, action):
        if not self.owns(job_id):
            return self.send_json(404, {"error": "Job not found."})
        data = {}
        if action in ("render", "preview"):
            try:
                request = self.read_body()
                hook = request["hook"]
                if type(hook) is not int or not 0 <= hook < 3:
                    raise ValueError
                data = {"hook": hook}
                if "voice" in request:
                    data["voice_override"] = request["voice"]
                if action == "render":
                    background = request["background"]
                    if background not in stories.CLIPS:
                        raise ValueError
                    data["background"] = background
                    style = request.get("caption_style", "bold")
                    if style not in ("bold", "calm"):
                        raise ValueError
                    data["caption_style"] = style
            except (ValueError, KeyError, TypeError):
                return self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Choose a valid opening and background."})
        elif action == "edit":
            try:
                request = self.read_body()
                section, index, text = request["section"], request["index"], request["text"]
                if section not in ("hooks", "setup", "ending") or type(index) is not int or index < 0 or not isinstance(text, str) or not 0 < len(text.strip()) <= 175:
                    raise ValueError
                data = {"section": section, "index": index, "text": text.strip()}
            except (ValueError, KeyError, TypeError):
                return self.send_json(400, {"error": "Choose a sentence and enter up to 175 characters."})
        elif action == "rewrite":
            try:
                request = self.read_body()
                scope, instruction = request["scope"], request["instruction"]
                if (scope not in ("opening", "ending", "characters", "custom")
                        or not isinstance(instruction, str) or not 0 < len(instruction.strip()) <= 600):
                    raise ValueError
                data = {"scope": scope, "instruction": instruction.strip()}
            except (ValueError, KeyError, TypeError):
                return self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Choose an edit type and enter up to 600 characters."})
        with LOCK:
            job = JOBS.get(job_id)
            if not job or job.get("kind") != "story" or "story" not in job or job["status"] not in ("pitch", "ready", "error"):
                data = None
            elif action == "edit" and data["index"] >= len(job["story"][data["section"]]):
                data = None
            else:
                self.original_job = copy.deepcopy(job)
                voice = data.pop("voice_override", job["voice"])
                if not isinstance(voice, str) or voice not in stories.available_voices(job.get("language", "en")):
                    return self.send_json(400, {"error": "Choose a narrator for this story's language."})
                job["voice"] = voice
                data = {**data, "story": job["story"], "voice": job["voice"],
                        "duration": job.get("duration", 60), "language": job.get("language", "en")}
                job.update(status="queued", detail="Story queued…")
                if action in ("render", "preview"):
                    job["selected_hook"] = data["hook"]
                if action == "render":
                    job.update(background=data["background"], caption_style=data["caption_style"])
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
            JOBS[job_id] = {"owner": self.owner, "status": "queued", "detail": "Waiting for the local writer…"}
        self.save_submission(job_id)
        WORKER.submit(write_pitch, job_id, idea.strip())
        self.send_json(HTTPStatus.ACCEPTED, {"id": job_id})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--stories-only", action="store_true", help="Serve only the cloud story studio.")
    options = parser.parse_args()
    if options.host not in ("127.0.0.1", "localhost", "::1") and (not os.environ.get("FRAMELET_ACCESS_TOKEN") or os.environ.get("FRAMELET_HTTPS_PROXY") != "1"):
        parser.error("Remote beta requires FRAMELET_ACCESS_TOKEN and FRAMELET_HTTPS_PROXY=1 behind a private HTTPS ingress.")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    import fcntl
    process_lock = (OUTPUT / "server.lock").open("a")
    try:
        fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("This data directory is already served by another Framelet process.")
    JOBS.update(store().load())
    App.stories_only = options.stories_only
    print(f"Framelet Studio · http://{options.host}:{options.port}", flush=True)
    ThreadingHTTPServer((options.host, options.port), App).serve_forever()
