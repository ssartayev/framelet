import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from env_config import load_env
from story_quality import review_story, CHECKS


class RecoveryTests(unittest.TestCase):
    def test_editor_falls_back_when_gemini_times_out(self):
        response = {"checks": dict.fromkeys(CHECKS, True), "issues": []}
        with patch("story_quality.write_with_api", side_effect=[RuntimeError("timeout"), response]) as call:
            result = review_story({}, "ru", {"GROQ_API_KEY": "fixture", "GEMINI_API_KEY": "fixture"})
        self.assertEqual(result["provider"], "groq")
        self.assertEqual([c.args[0] for c in call.call_args_list], ["gemini", "groq"])

    def test_explicit_editor_does_not_change_provider(self):
        with patch("story_quality.write_with_api", side_effect=RuntimeError("timeout")) as call:
            with self.assertRaises(RuntimeError):
                review_story({}, "ru", {"GROQ_API_KEY": "fixture", "GEMINI_API_KEY": "fixture",
                                        "FRAMELET_STORY_REVIEW_PROVIDER": "gemini"})
        self.assertEqual(call.call_count, 1)

    def test_env_is_literal_and_does_not_override_host(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"EXISTING": "host"}, clear=True):
            path = Path(folder) / ".env"
            path.write_text('EXISTING=file\nexport NEW="literal $(echo test)"\n# ignored\n')
            load_env(path)
            self.assertEqual(os.environ["EXISTING"], "host")
            self.assertEqual(os.environ["NEW"], "literal $(echo test)")
