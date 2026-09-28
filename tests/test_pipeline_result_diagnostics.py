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
    _telegram_text_chunks,
    _write_pipeline_result,
    process_text,
)


class _TestWorkerLog:
    def __init__(self):
        self.messages = []

    def __call__(self, message):
        self.messages.append(str(message))

    def close_stage(self):
        pass

    def flush_progress(self):
        pass

    def finish_timing(self, _status="SUCCESS"):
        pass


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

    def test_telegram_update_id_is_safely_recorded_for_traceability(self):
        document = _pipeline_result_document(
            "text", "dengeli", result={}, telegram_update_id=987654321,
        )
        self.assertEqual(document["telegram_update_id"], "987654321")

        invalid = _pipeline_result_document("text", "dengeli", result={}, telegram_update_id="not-an-id")
        self.assertNotIn("telegram_update_id", invalid)

    def test_long_telegram_text_is_split_without_loss_or_oversize_parts(self):
        text = ("Birinci satır uzun içerik.\n" * 250) + ("son " * 500)

        chunks = _telegram_text_chunks(text, limit=256)

        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk) <= 256 for chunk in chunks))
        self.assertEqual("".join(chunks), text)

    def test_text_mode_delivers_outputs_independently_and_records_status(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / "tts.wav"
            wav_path.write_bytes(b"RIFF-test-wave")
            result = {
                "reels_aciklamasi": "Instagram caption metni.",
                "reels_hashtagleri": ["#otomobil", "#otoXtra"],
                "threads_aciklamasi": "Threads fikri.",
                "kapak_basliklari": [{"ust": "FİYAT ŞOKU", "alt": "Gerçek kullanım hesabı"}],
                "seslendirme_metni": "Bu örnek seslendirme metnidir.",
                "ses_modu": "SOLO_FEMALE",
                "ses_modu_sesi": "Autonoe",
                "ses_basarili": True,
                "ses_dosyasi": str(wav_path),
                "qa_result": {"overall": "PASS"},
                "qa_pass": True,
            }
            sent_messages = []
            edited_reports = []
            written_documents = []
            worker_log = _TestWorkerLog()

            def fake_send_message(text):
                text = str(text)
                if "Metin alındı, text-only pipeline" in text:
                    return {"result": {"message_id": 123}}
                sent_messages.append(text)
                if "Threads fikri." in text:
                    raise RuntimeError("Threads teslimatı başarısız")
                return {"ok": True}

            with patch("telegram.telegram_pipeline_worker.SmartRouter", return_value=object()), \
                 patch("telegram.telegram_pipeline_worker._setup_env", return_value=(
                     {}, [], [], "dengeli", "balanced", worker_log, lambda *_args, **_kwargs: None,
                 )), \
                 patch("telegram.telegram_pipeline_worker.metin_pipeline_calistir", return_value=result), \
                 patch("telegram.telegram_pipeline_worker.send_message", side_effect=fake_send_message), \
                 patch("telegram.telegram_pipeline_worker.send_document", return_value={"ok": True}) as send_document, \
                 patch("telegram.telegram_pipeline_worker.edit_message", side_effect=lambda _id, text: edited_reports.append(text)), \
                 patch("telegram.telegram_pipeline_worker._write_pipeline_result", side_effect=lambda document, *_a, **_kw: written_documents.append(document)):
                process_text("BYD örnek metin")

            self.assertTrue(any("Instagram caption metni." in item and "#otoXtra" in item for item in sent_messages))
            self.assertTrue(any("KAPAK BAŞLIĞI ALTERNATİFLERİ" in item for item in sent_messages))
            self.assertTrue(any("Bu örnek seslendirme metnidir." in item for item in sent_messages))
            self.assertEqual(send_document.call_count, 1)
            self.assertEqual(len(edited_reports), 1)
            self.assertIn("TEXT-ONLY PIPELINE RAPORU", edited_reports[0])
            self.assertIn("8/8", edited_reports[0])

            document = written_documents[0]
            self.assertEqual(document["run_status"], "telegram_delivery_error")
            self.assertEqual(document["telegram_delivery_status"]["caption"], "delivered")
            self.assertEqual(document["telegram_delivery_status"]["title_options"], "delivered")
            self.assertEqual(document["telegram_delivery_status"]["threads"], "failed")
            self.assertEqual(document["telegram_delivery_status"]["voiceover"], "delivered")
            self.assertEqual(document["telegram_delivery_status"]["tts_wave"], "delivered")
            self.assertIn("Bu örnek seslendirme metnidir.", document["voiceover"])
            self.assertTrue(document["tts_wave_ready"])
            self.assertNotIn(str(wav_path), json.dumps(document, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
