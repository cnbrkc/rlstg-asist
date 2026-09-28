"""Failure diagnostics are written atomically without changing pipeline results."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test-only-not-a-real-key")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram.telegram_pipeline_worker import (
    _pipeline_result_document,
    _write_pipeline_result,
)


class PipelineResultDiagnosticsTests(unittest.TestCase):
    def test_failure_document_keeps_useful_context_and_status(self):
        result = {
            "final_video": "",
            "editorial_brief": {
                "selected_story_category": "fiyat_deger",
                "_runtime_priority_audit": {"status": "aligned"},
            },
            "pipeline_state": {"reels_state": {"turkiye_ilgi_kancasi": "Doğrulanmış sinyal."}},
            "seslendirme_metni": "Örnek metin.",
            "qa_result": {"overall": "FAIL"},
            "qa_pass": False,
        }
        document = _pipeline_result_document(
            "video.mp4", "teknik", result=result,
            warnings=["uyarı"], errors=["render başarısız"],
            run_status="no_final_video", video_note="analiz notu",
        )

        self.assertEqual(document["run_status"], "no_final_video")
        self.assertEqual(document["selected_story_category"], "fiyat_deger")
        self.assertEqual(document["turkiye_ilgi_kancasi"], "Doğrulanmış sinyal.")
        self.assertEqual(document["qa"]["overall"], "FAIL")
        self.assertEqual(document["errors"], ["render başarısız"])
        self.assertEqual(document["video_note"], "analiz notu")

    def test_result_json_is_written_atomically_and_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pipeline_result.json"
            logs = []
            document = {"run_status": "pipeline_error", "errors": ["örnek"], "unicode": "ş"}

            self.assertTrue(_write_pipeline_result(document, logs.append, path=path))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), document)
            self.assertEqual(logs, [])
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_write_failure_does_not_leave_a_partial_document_or_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pipeline_result.json"
            path.write_text("previous result", encoding="utf-8")
            logs = []

            with patch("telegram.telegram_pipeline_worker.os.replace", side_effect=OSError("disk error")):
                self.assertFalse(_write_pipeline_result({"new": True}, logs.append, path=path))

            self.assertEqual(path.read_text(encoding="utf-8"), "previous result")
            self.assertEqual(list(Path(directory).iterdir()), [path])
            self.assertEqual(len(logs), 1)
            self.assertIn("tanı dosyası yazılamadı", logs[0])

    def test_text_document_keeps_existing_social_fields(self):
        document = _pipeline_result_document(
            "text", "dengeli", result={"kapak_basliklari": [{"ust": "BAŞLIK"}]},
            caption="Caption", threads="Threads", run_status="success",
        )
        self.assertEqual(document["source"], "text")
        self.assertEqual(document["caption"], "Caption")
        self.assertEqual(document["threads"], "Threads")
        self.assertEqual(document["title_options"][0]["ust"], "BAŞLIK")
        self.assertEqual(document["run_status"], "success")


if __name__ == "__main__":
    unittest.main()
