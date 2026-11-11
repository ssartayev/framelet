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
from urllib.request import Request, urlopen

import app
import stories

STORY = {
    "title": "Ключи от мастерской",
    "hooks": ["Отец положил ключи на верстак.", "Я узнал связку отцовских ключей.", "Ключи звякнули о мой верстак."],
    "setup": ["Брат остался без работы, и отец обещал ему место в моей мастерской."],
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


class NarrationTests(unittest.TestCase):
    def test_cloud_uses_one_script_and_adds_sentence_pauses(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(stories, "cloud_narration", return_value=bytes(48_000)) as synthesize:
            directory = Path(folder)
            cues = stories.narrate(directory, "preview.wav", ["Он кивнул", "Она спросила: «Зачем?»"], "elevenlabs", "ru")
            synthesize.assert_called_once_with("Он кивнул. Она спросила: «Зачем?»", "ru")
            with wave.open(str(directory / "preview.wav"), "rb") as audio:
                self.assertEqual(audio.getnframes() / audio.getframerate(), 1)
            self.assertEqual(cues, [(0, 1, "Он кивнул. Она спросила: «Зачем?»")])


class EditorHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(app, "OUTPUT", Path(temporary)))
        self.enterContext(patch.object(app, "JOBS", {}))
        self.enterContext(patch.object(stories, "ready", return_value=True))
        self.enterContext(patch.object(stories, "available_voices", return_value=stories.VOICES))
        self.writer = self.enterContext(patch.object(stories, "write_story", return_value=STORY))
        self.narrator = self.enterContext(patch.object(stories, "narrate", side_effect=self.fake_audio))
        self.server = self.enterContext(app.ThreadingHTTPServer(("127.0.0.1", 0), app.App))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    @staticmethod
    def fake_audio(directory, filename, lines, voice, language):
        (directory / filename).write_bytes(b"preview")

    def request(self, path, data=None):
        body = None if data is None else json.dumps(data).encode()
        with urlopen(Request(self.base + path, data=body), timeout=3) as response:
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
            "idea": idea, "category": "Family secrets", "voice": "am_michael", "language": "ru", "duration": 30,
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
        with urlopen(self.base + preview, timeout=3) as response:
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
                    "voice": "am_michael", "language": "ru", "duration": 30,
                })
                self.assertEqual(self.wait_for(job["id"], "pitch")["story"], stories.PREMISES[entry["id"]]["story"])
        self.writer.assert_not_called()
        self.narrator.assert_not_called()

    def test_invalid_prepared_story_requests_are_rejected(self):
        request = {"premise": "brother-job", "category": "Family secrets", "voice": "am_michael",
                   "language": "ru", "duration": 30}
        for override in ({"premise": "missing"}, {"premise": []}, {"language": "en"}, {"duration": 60}):
            with self.subTest(override=override), self.assertRaises(HTTPError) as error:
                self.request("/api/stories", {**request, **override})
            self.assertEqual(error.exception.code, 400)
        self.writer.assert_not_called()

    def test_unicode_source_text_fits_request_limit(self):
        source = "П" * 4000
        self.draft(source)
        self.assertEqual(self.writer.call_args.args[1]["idea"], source)


if __name__ == "__main__":
    unittest.main()
