"""Telegram worker: bot token'ı hiçbir log/rapor/artifact'a sızmamalı ve kapak
alternatifleri kullanıcıya Üst/Alt etiketi olmadan sunulmalı."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:AAtest-token-degeri-uzun-olsun")
os.environ.setdefault("TELEGRAM_CHAT_ID", "42")

from telegram import telegram_pipeline_worker as w


TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]


class ScrubTests(unittest.TestCase):
    def test_scrub_masks_env_token(self):
        metin = f"hata: 400 for url: https://api.telegram.org/bot{TOKEN}/sendMessage"
        temiz = w._scrub(metin)
        self.assertNotIn(TOKEN, temiz)
        self.assertIn("bot***", temiz)

    def test_scrub_masks_generic_bot_token_even_without_env(self):
        temiz = w._scrub("url https://api.telegram.org/bot111222333:AAAAAAAAAAbbbbbbbbbbcccccccccc/sendMessage")
        self.assertNotIn("AAAAAAAAAAbbbbbbbbbbcccccccccc", temiz)
        self.assertIn("bot111222333:***", temiz)

    def test_telegram_api_hatasi_has_no_token_or_url(self):
        class _Yanit:
            ok = False
            status_code = 400

            def json(self):
                return {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}

        hata = w._telegram_api_hatasi(_Yanit(), "sendMessage")
        mesaj = str(hata)
        self.assertNotIn(TOKEN, mesaj)
        self.assertNotIn("api.telegram.org", mesaj)
        self.assertIn("chat not found", mesaj)


class ReportScrubTests(unittest.TestCase):
    def test_final_report_scrubs_token_in_errors(self):
        hatali = f"❌ 400 Client Error for url: https://api.telegram.org/bot{TOKEN}/sendMessage"
        rapor = w._final_report({}, [], [hatali], {}, "dengeli")
        self.assertNotIn(TOKEN, rapor)

    def test_pipeline_result_document_scrubs_errors(self):
        hatali = f"RuntimeError: bot{TOKEN}/getUpdates failed"
        doc = w._pipeline_result_document("video", "dengeli", errors=[hatali])
        self.assertNotIn(TOKEN, "\n".join(doc["errors"]))


class TitleFormatTests(unittest.TestCase):
    def test_no_ust_alt_labels_in_telegram_message(self):
        basliklar = [
            {"ust": "FİYAT ŞOKU", "alt": "Bu fiyat listesi herkesi şaşırttı."},
            {"ust": "SON DAKİKA", "alt": "Yeni tablo eski araçları da vurdu."},
        ]
        mesaj = w._format_title_options(basliklar)
        self.assertNotIn("Üst:", mesaj)
        self.assertNotIn("Alt:", mesaj)
        self.assertIn("FİYAT ŞOKU", mesaj)
        self.assertIn("Bu fiyat listesi herkesi şaşırttı.", mesaj)
        self.assertIn("SON DAKİKA", mesaj)

    def test_string_only_titles_still_render(self):
        mesaj = w._format_title_options(["SADECE ÜST"])
        self.assertIn("SADECE ÜST", mesaj)
        self.assertNotIn("Üst:", mesaj)


class StepCounterTests(unittest.TestCase):
    def test_adim_sayaci_sabit_degil_listeden_turer(self):
        rapor = w._final_report({}, [], [], {}, "dengeli")
        son_adim = f"{len(w.PIPELINE_STEPS)}/{len(w.PIPELINE_STEPS)}"
        self.assertIn(son_adim, rapor)
        # Metin modu raporu kendi 8 adımlı listesini kullanır.
        rapor_text = w._text_final_report({}, [], [], {}, "dengeli", {})
        self.assertIn(f"{len(w.TEXT_PIPELINE_STEPS)}/{len(w.TEXT_PIPELINE_STEPS)}", rapor_text)


if __name__ == "__main__":
    unittest.main()
