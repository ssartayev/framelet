import base64
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_store import JobStore
from story_captions import alignment_words
from story_quality import valid_review, CHECKS
from story_validation import story_is_reasonable
import stories
import story_writer
from test_story_editor import STORY, elevenlabs_response


class PersistenceTests(unittest.TestCase):
    def test_restart_preserves_draft_and_interrupts_pending_work(self):
        with tempfile.TemporaryDirectory() as folder:
            store = JobStore(Path(folder) / "jobs.sqlite3")
            store.save("one", {"status": "rendering", "story": STORY, "owner": "alice"},
                       ("alice", "request-123", "digest"))
            recovered = JobStore(store.path).load()
            self.assertEqual(recovered["one"]["story"], STORY)
            self.assertEqual(recovered["one"]["status"], "error")
            self.assertEqual(store.response("alice", "request-123"), ("digest", {"id": "one"}))
            self.assertIsNone(store.response("bob", "request-123"))

    def test_completed_project_survives_restart_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            store = JobStore(Path(folder) / "db")
            job = {"status": "ready", "story": STORY, "video": "/film.mp4"}
            store.save("one", job)
            self.assertEqual(store.load()["one"], job)


class ProviderRobustnessTests(unittest.TestCase):
    def test_malformed_gemini_response_is_retryable_provider_error(self):
        for parts in (["bad"], None, [{"text": 42}]):
            with self.subTest(parts=parts), patch("story_writer.urlopen", side_effect=lambda *a, **k: io.BytesIO(
                json.dumps({"candidates": [{"content": {"parts": parts}}]}).encode())):
                with self.assertRaises(RuntimeError):
                    story_writer.write_with_api("gemini", "test", {}, lambda v: False, "fixture")

    def test_alignment_requires_monotonic_time_and_spoken_words(self):
        for chars, starts in [(list("hello"), [0, .4, .3, .2, .1]), (["."], [0]), (["x"], [float("nan")])]:
            with self.subTest(chars=chars), self.assertRaises(ValueError):
                alignment_words({"characters": chars, "character_start_times_seconds": starts}, 1)

    def test_editor_cannot_pass_with_unresolved_issues(self):
        checks = dict.fromkeys(CHECKS, True)
        self.assertTrue(valid_review({"checks": checks, "issues": []}))
        self.assertFalse(valid_review({"checks": checks, "issues": ["Unresolved problem"]}))
        self.assertFalse(valid_review({"checks": {**checks, "continuity": False}, "issues": []}))

    def test_tiny_script_is_not_a_two_minute_story(self):
        self.assertFalse(story_is_reasonable(STORY, 120))

    def test_audio_is_reused_and_invalidated_by_voice_or_text(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "ELEVENLABS_API_KEY": "fixture", "FRAMELET_ELEVENLABS_VOICE_RU": "voice-one",
        }), patch("stories.urlopen", side_effect=lambda *a, **k: io.BytesIO(
            json.dumps(elevenlabs_response("Он кивнул.")).encode())) as send:
            directory = Path(folder)
            stories.narrate(directory, "one.wav", ["Он кивнул."], "elevenlabs", "ru")
            stories.narrate(directory, "two.wav", ["Он кивнул."], "elevenlabs", "ru")
            self.assertEqual(send.call_count, 1)
            self.assertEqual((directory / "one.wav").read_bytes(), (directory / "two.wav").read_bytes())
            with patch.dict(os.environ, FRAMELET_ELEVENLABS_VOICE_RU="voice-two"):
                stories.narrate(directory, "three.wav", ["Он кивнул."], "elevenlabs", "ru")
            self.assertEqual(send.call_count, 2)
            stories.narrate(directory, "four.wav", ["Он спросил."], "elevenlabs", "ru")
            self.assertEqual(send.call_count, 3)

    def test_failed_editor_keeps_old_story_and_does_not_narrate(self):
        bad = {"checks": {**dict.fromkeys(CHECKS, True), "continuity": False}, "issues": ["Gender changes."]}
        result = type("Result", (), {"stdout": json.dumps(STORY)})()
        with patch("stories.subprocess.run", return_value=result), patch("stories.review_story", return_value=bad), \
             self.assertRaisesRegex(stories.NarrationError, "did not pass"):
            stories.write_story("draft", {"duration": 30, "language": "ru"}, {})
