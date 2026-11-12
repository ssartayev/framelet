import base64
import io
import json
import os
import runpy
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar

import app
import stories

STORY = {
    "title": "Ключи от мастерской",
    "hooks": ["Отец положил ключи на верстак.", "Я узнал связку отцовских ключей.", "Ключи звякнули о мой верстак."],
    "setup": ["Брат остался без работы, и отец обещал ему место в моей мастерской.", "Отец даже не спросил меня, нужна ли помощь.", "Я отложил инструменты и попросил брата поговорить без отца."],
    "ending": ["Я предложил брату начать учеником.", "Брат попросил выдать ему перчатки."],
}
ENDING = ["Брат признался, что хочет уйти из семьи.", "Я помог ему снять комнату рядом."]
OPENINGS = ["Я увидел отца у верстака.", "Отец впервые зашёл в мою мастерскую.", "Я услышал знакомый звон у двери."]


class WriterTests(unittest.TestCase):
    def rewrite(self, scope, *outputs):
        request_body = {"story": STORY, "scope": scope, "instruction": "Добавь больше конкретики.",
                        "language": "ru", "duration": 30}
        responses = [
            io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(output)}}]}).encode())
            for output in outputs
        ]
        stdout = io.StringIO()
        with (
            patch.dict(os.environ, {"GROQ_API_KEY": "test", "FRAMELET_STORY_REWRITE_PROVIDER": "groq"}),
            patch.object(sys, "argv", ["story_writer.py", "rewrite"]),
            patch.object(sys, "stdin", io.StringIO(json.dumps(request_body))),
            patch.object(sys, "stdout", stdout),
            patch("urllib.request.urlopen", side_effect=responses) as send,
        ):
            runpy.run_path("story_writer.py", run_name="__main__")
        messages = json.loads(send.call_args.args[0].data)["messages"]
        self.assertIn("Framelet plot rules", messages[0]["content"])
        self.assertIn(request_body["instruction"], messages[1]["content"])
        return json.loads(stdout.getvalue())

    def test_ending_edit_preserves_openings_and_setup(self):
        self.assertEqual(
            self.rewrite("ending", {"ending": ENDING, "setup": ["Unrequested change."]}),
            {**STORY, "ending": ENDING},
        )

    def test_opening_edit_preserves_story_body_and_retries_noop(self):
        self.assertEqual(self.rewrite("opening", STORY, {"hooks": OPENINGS}), {**STORY, "hooks": OPENINGS})

    def test_full_rewrite_returns_complete_story(self):
        revised = {**STORY, "title": "Ученик", "ending": ENDING}
        for scope in ("characters", "custom"):
            with self.subTest(scope=scope):
                self.assertEqual(self.rewrite(scope, revised), revised)

    def test_rejects_repetitive_edit(self):
        with self.assertRaisesRegex(SystemExit, "format and length limits"):
            self.rewrite("ending", {"ending": ENDING * 10}, {"ending": ENDING * 10})


    def test_cloud_fallback_from_groq_to_gemini(self):
        body = {"idea": "Ключи от мастерской", "language": "ru", "duration": 30}
        gemini = {"candidates": [{"content": {"parts": [
            {"text": "ignored", "thought": True}, {"text": json.dumps(STORY)},
        ]}}]}
        stdout = io.StringIO()
        with (
            patch.dict(os.environ, {"GROQ_API_KEY": "test", "GEMINI_API_KEY": "test"}, clear=True),
            patch.object(sys, "argv", ["story_writer.py", "draft"]),
            patch.object(sys, "stdin", io.StringIO(json.dumps(body))),
            patch.object(sys, "stdout", stdout),
            patch("urllib.request.urlopen", side_effect=[
                HTTPError("", 429, "", {}, None), io.BytesIO(json.dumps(gemini).encode()),
            ]) as send,
        ):
            runpy.run_path("story_writer.py", run_name="__main__")
        self.assertEqual(json.loads(stdout.getvalue()), STORY)
        self.assertEqual([call.args[0].host for call in send.call_args_list],
                         ["api.groq.com", "generativelanguage.googleapis.com"])

    def test_missing_keys_do_not_load_local_model(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(sys, "argv", ["story_writer.py", "draft"]),
            patch.object(sys, "stdin", io.StringIO("{}")),
            self.assertRaisesRegex(SystemExit, "needs GROQ_API_KEY"),
        ):
            runpy.run_path("story_writer.py", run_name="__main__")


def elevenlabs_response(script, duration=2):
    return {
        "audio_base64": base64.b64encode(bytes(int(duration * stories.RATE) * 2)).decode(),
        "alignment": {
            "characters": list(script),
            "character_start_times_seconds": [duration * i / len(script) for i in range(len(script))],
        },
    }


class NarrationTests(unittest.TestCase):
    def test_cloud_audio_and_timings_use_one_request(self):
        script = "Он кивнул. Она спросила: «Зачем?»"
        payload = elevenlabs_response(script)
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, ELEVENLABS_API_KEY="test", FRAMELET_ELEVENLABS_VOICE_RU="test-voice"),
            patch("stories.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())) as send,
        ):
            directory = Path(folder)
            cues, words = stories.narrate(directory, "preview.wav", ["Он кивнул", "Она спросила: «Зачем?»"], "elevenlabs", "ru")
            request = send.call_args.args[0]
            self.assertIn("/with-timestamps?output_format=pcm_24000", request.full_url)
            self.assertEqual(json.loads(request.data)["text"], script)
            with wave.open(str(directory / "preview.wav"), "rb") as audio:
                self.assertEqual(audio.getnframes() / audio.getframerate(), 2)
            self.assertEqual(cues, [(0, 2, script)])
            self.assertEqual([item["word"] for item in words], script.split())

    def test_corrupt_alignment_is_a_provider_error(self):
        payload = elevenlabs_response("Hello.")
        payload["alignment"]["character_start_times_seconds"][1] = -1
        with (
            patch.dict(os.environ, ELEVENLABS_API_KEY="test", FRAMELET_ELEVENLABS_VOICE_EN="test-voice"),
            patch("stories.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())),
            self.assertRaisesRegex(stories.NarrationError, "invalid audio or caption timing"),
        ):
            stories.cloud_narration("Hello.", "en")

    def test_readiness_is_based_on_cloud_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(stories.ready("ru"))
            self.assertEqual(stories.available_voices("ru"), {})
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "test", "ELEVENLABS_API_KEY": "test", "FRAMELET_ELEVENLABS_VOICE_RU": "test",
        }, clear=True):
            self.assertTrue(stories.ready("ru"))
            self.assertFalse(stories.ready("en"))


class EditorHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(app, "OUTPUT", Path(temporary)))
        self.enterContext(patch.object(app, "JOBS", {}))
        self.enterContext(patch.object(stories, "ready", return_value=True))
        self.enterContext(patch.object(stories, "available_voices", return_value=stories.VOICES))
        self.writer = self.enterContext(patch.object(stories, "write_story", return_value=STORY))
        self.narrator = self.enterContext(patch.object(stories, "narrate", side_effect=self.fake_audio))
        self.enterContext(patch.object(app.App, "stories_only", True))
        self.server = self.enterContext(app.ThreadingHTTPServer(("127.0.0.1", 0), app.App))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    @staticmethod
    def fake_audio(directory, filename, lines, voice, language):
        (directory / filename).write_bytes(b"preview")

    def request(self, path, data=None):
        body = None if data is None else json.dumps(data).encode()
        with self.opener.open(Request(self.base + path, data=body), timeout=3) as response:
            return json.load(response)

    def wait_for(self, job_id, state):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = self.request(f"/api/jobs/{job_id}")
            if result["status"] == state:
                return result
            if result["status"] == "error":
                self.fail(result["detail"])
            time.sleep(0.02)
        self.fail(f"Job did not reach {state}")

    def draft(self, idea="Ключи от мастерской"):
        result = self.request("/api/stories", {
            "idea": idea, "category": "Family secrets", "voice": "elevenlabs", "language": "ru", "duration": 30,
        })
        self.wait_for(result["id"], "pitch")
        return result["id"]

    def test_review_and_rewrite_then_record_only_selected_opening(self):
        job_id = self.draft()
        self.narrator.assert_not_called()
        self.writer.return_value = {**STORY, "hooks": OPENINGS}
        self.request(f"/api/stories/{job_id}/rewrite", {"scope": "opening", "instruction": "Добавь звук в начале."})
        self.assertEqual(self.wait_for(job_id, "pitch")["story"]["hooks"], OPENINGS)
        self.narrator.assert_not_called()
        self.request(f"/api/stories/{job_id}/preview", {"hook": 1})
        preview = self.wait_for(job_id, "pitch")["preview"]
        self.assertEqual(self.narrator.call_args.args[2], [OPENINGS[1]])
        with self.opener.open(self.base + preview, timeout=3) as response:
            self.assertEqual(response.headers.get_content_type(), "audio/wav")
            self.assertEqual(response.read(), b"preview")

    def test_rejected_requests_do_not_change_story(self):
        job_id = self.draft()
        for route, data in (
            ("rewrite", {"scope": "ending", "instruction": " "}),
            ("rewrite", {"scope": "invalid", "instruction": "Rewrite"}),
            ("rewrite", {"scope": "custom", "instruction": "a" * 601}),
            ("preview", {"hook": 3}),
        ):
            with self.subTest(route=route, data=data), self.assertRaises(HTTPError) as error:
                self.request(f"/api/stories/{job_id}/{route}", data)
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.wait_for(job_id, "pitch")["story"], STORY)
        self.writer.assert_called_once()

    def test_failed_rewrite_keeps_previous_version(self):
        job_id = self.draft()
        self.writer.return_value = {**STORY, "ending": ENDING * 10}
        self.request(f"/api/stories/{job_id}/rewrite", {"scope": "ending", "instruction": "Добавь развязку."})
        self.assertEqual(self.wait_for(job_id, "error")["story"], STORY)
        self.narrator.assert_not_called()

    def test_prepared_catalog_opens_without_writer_or_narrator(self):
        options = self.request("/api/stories/options")
        self.assertGreaterEqual(len(options["premises"]), 6)
        self.assertTrue(options["preparedStoryReady"])
        for entry in options["premises"]:
            with self.subTest(premise=entry["id"]):
                job = self.request("/api/stories", {
                    "premise": entry["id"], "category": entry["category"],
                    "voice": "elevenlabs", "language": "ru", "duration": 30,
                })
                self.assertEqual(self.wait_for(job["id"], "pitch")["story"], stories.PREMISES[entry["id"]]["story"])
        self.writer.assert_not_called()
        self.narrator.assert_not_called()

    def test_invalid_prepared_story_requests_are_rejected(self):
        request = {"premise": "brother-job", "category": "Family secrets", "voice": "elevenlabs",
                   "language": "ru", "duration": 30}
        for override in ({"premise": "missing"}, {"premise": []}, {"language": "en"}, {"duration": 60}):
            with self.subTest(override=override), self.assertRaises(HTTPError) as error:
                self.request("/api/stories", {**request, **override})
            self.assertEqual(error.exception.code, 400)
        self.writer.assert_not_called()

    def test_cloud_deployment_routes_and_https_origin(self):
        with urlopen(self.base + "/", timeout=3) as response:
            self.assertTrue(response.url.endswith("/stories.html"))
        self.assertTrue(self.request("/api/stories/options")["storiesOnly"])
        with self.assertRaises(HTTPError) as error:
            self.request("/api/pitch", {"idea": "test"})
        self.assertEqual(error.exception.code, 404)
        request = Request(self.base + "/api/stories", data=json.dumps({
            "idea": "Test", "category": "Family secrets", "voice": "elevenlabs", "duration": 30,
        }).encode(), headers={"Origin": self.base.replace("http:", "https:")})
        with self.opener.open(request, timeout=3) as response:
            self.assertEqual(response.status, 202)
            self.wait_for(json.load(response)["id"], "pitch")

    def test_unicode_source_text_fits_request_limit(self):
        source = "П" * 4000
        self.draft(source)
        self.assertEqual(self.writer.call_args.args[1]["idea"], source)

    def test_other_browser_cannot_read_edit_or_download_project(self):
        job_id = self.draft()
        for path, payload in ((f"/api/jobs/{job_id}", None),
                              (f"/films/{job_id}/film.mp4", None),
                              (f"/api/stories/{job_id}/twist", {})):
            request = Request(self.base + path, data=None if payload is None else json.dumps(payload).encode())
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=3)
            self.assertEqual(error.exception.code, 404)

    def test_idempotent_create_survives_restart_and_rejects_body_change(self):
        self.request("/api/stories/options")
        data = {"idea": "Keys", "category": "Family secrets", "voice": "elevenlabs", "language": "ru", "duration": 30}
        def submit(body):
            request = Request(self.base + "/api/stories", data=json.dumps(body).encode(),
                              headers={"Idempotency-Key": "same-request-123"})
            with self.opener.open(request, timeout=3) as response:
                return json.load(response)
        first = submit(data)
        self.wait_for(first["id"], "pitch")
        with app.LOCK:
            app.JOBS.clear()
            app.JOBS.update(app.store().load())
        self.assertEqual(submit(data), first)
        self.writer.assert_called_once()
        with self.assertRaises(HTTPError) as error:
            submit({**data, "idea": "different"})
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.request("/api/projects")["projects"][0]["id"], first["id"])

    def test_queue_rejects_overload_before_spending(self):
        with patch.object(app, "MAX_PENDING", 0), self.assertRaises(HTTPError) as error:
            self.draft()
        self.assertEqual(error.exception.code, 429)
        self.writer.assert_not_called()

    def test_sentence_edit_preserves_other_sections_and_version(self):
        job_id = self.draft()
        self.request(f"/api/stories/{job_id}/edit", {"section": "ending", "index": 0,
                                                    "text": "Я предложил брату сначала поработать рядом со мной."})
        job = self.wait_for(job_id, "pitch")
        self.assertEqual(job["story"]["setup"], STORY["setup"])
        self.assertEqual(job["story"]["hooks"], STORY["hooks"])
        self.assertEqual(job["story"]["ending"][1], STORY["ending"][1])
        self.assertEqual(job["versions"][-1]["story"], STORY)
        self.writer.assert_called_once()
        self.narrator.assert_not_called()

    def test_beta_access_token_protects_options_and_post(self):
        import base64
        with patch.dict(os.environ, FRAMELET_ACCESS_TOKEN="fixture-password"):
            with self.assertRaises(HTTPError) as error:
                self.request("/api/stories/options")
            self.assertEqual(error.exception.code, 401)
            request = Request(self.base + "/api/stories/options", headers={"Authorization": "Basic " +
                              base64.b64encode(b"beta:fixture-password").decode()})
            with self.opener.open(request, timeout=3) as response:
                self.assertEqual(response.status, 200)

    def test_database_failure_does_not_leave_queued_jobs(self):
        import sqlite3
        from job_store import JobStore
        with patch.object(JobStore, "save", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(HTTPError) as error:
                self.draft()
            self.assertEqual(error.exception.code, 503)
        self.assertEqual(app.JOBS, {})
        self.writer.assert_not_called()

    def test_database_failure_restores_existing_project(self):
        import sqlite3
        from job_store import JobStore
        job_id = self.draft()
        before = self.request(f"/api/jobs/{job_id}")
        with patch.object(JobStore, "save", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(HTTPError) as error:
                self.request(f"/api/stories/{job_id}/twist", {})
            self.assertEqual(error.exception.code, 503)
        self.assertEqual(self.request(f"/api/jobs/{job_id}"), before)
        self.writer.assert_called_once()

    def test_bad_basic_credentials_are_denied(self):
        with patch.dict(os.environ, FRAMELET_ACCESS_TOKEN="fixture-password"):
            for auth in ("Basic !!!", "Basic Zm9v", "Bearer anything"):
                with self.subTest(auth=auth), self.assertRaises(HTTPError) as error:
                    self.opener.open(Request(self.base + "/api/stories/options", headers={"Authorization": auth}), timeout=3)
                self.assertEqual(error.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
