"""Üretim sözleşmesi tutarlılığı: sayısal eşikler TEK kaynaktan (core/sozlesme.py)
türer. Prompt/kod kayması (#7/#8 tarzı üretici-hakem çelişkisi) burada kırılır."""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core import agentic, cover_titles, media, sozlesme

KOK = Path(__file__).resolve().parents[1]


class CaptionSozlesmeTests(unittest.TestCase):
    """Üretici (caption_prompt) ile hakem (qa_prompt) AYNI bandı kullanmalı.

    Eski kayma: üretici '650-750 olabilir' derken hakem '600 altı FAIL + 700
    altı da FAIL' diyordu → 680 karakterlik yasal caption zorunlu QA FAIL'e
    düşüyordu (non-blocking olduğu için video kurtuluyordu ama gereksiz
    yenileme turu yakıyordu).
    """

    def test_uretici_promptu_sozlesme_bandiyla_uyumlu(self):
        metin = (KOK / "core/prompts/caption_prompt.txt").read_text(encoding="utf-8")
        self.assertIn(f"{sozlesme.CAPTION_HEDEF_MIN}-{sozlesme.CAPTION_HEDEF_MAX} karakter hedefle", metin)
        self.assertIn(f"{sozlesme.CAPTION_ALT_SINIR} karaktere kadar inilebilir", metin)
        self.assertNotIn("650-750", metin)  # eski çelişkili ifade dönmemeli

    def test_hakem_promptu_ureticiyle_ayni_esigi_kullanir(self):
        metin = (KOK / "core/prompts/qa_prompt.txt").read_text(encoding="utf-8")
        esik = re.search(r"(\d{3}) karakterin ALTINDAYSA FAIL", metin)
        self.assertIsNotNone(esik, "QA promptunda net alt eşik bulunamadı")
        self.assertEqual(sozlesme.CAPTION_ALT_SINIR, int(esik.group(1)))
        self.assertIn(f"Hedef bant {sozlesme.CAPTION_HEDEF_MIN}-{sozlesme.CAPTION_HEDEF_MAX} karakterdir", metin)
        # 650-700 arası doğal caption artık FAIL değil (üreticiyle uyum).
        self.assertIn(f"{sozlesme.CAPTION_ALT_SINIR}-{sozlesme.CAPTION_HEDEF_MIN} arası", metin)

    def test_ust_sinir_uretici_promptunde(self):
        # Üst sınır üretici promptu için biçim kuralıdır ("900'ü aşma"); hakem
        # promptu bandın altını denetler (alt eşik testi ayrı).
        uretici = (KOK / "core/prompts/caption_prompt.txt").read_text(encoding="utf-8")
        self.assertIn(f"{sozlesme.CAPTION_UST_SINIR}'ü aşma", uretici)


class KapakSozlesmeTests(unittest.TestCase):
    def test_cover_titles_sabitleri_sozlesmeden_turer(self):
        self.assertEqual(sozlesme.KAPAK_UST_MIN_KELIME, cover_titles.UST_MIN_KELIME)
        self.assertEqual(sozlesme.KAPAK_UST_MAX_KELIME, cover_titles.UST_MAX_KELIME)
        self.assertEqual(sozlesme.KAPAK_ALT_MIN_KELIME, cover_titles.ALT_MIN_KELIME)
        self.assertEqual(sozlesme.KAPAK_ALT_MAX_KELIME, cover_titles.ALT_MAX_KELIME)
        self.assertEqual(sozlesme.KAPAK_ALTERNATIF_SAYISI, cover_titles.ALTERNATIF_SAYISI)


class SesSozlesmeTests(unittest.TestCase):
    def test_tts_sure_bandi_sozlesmeden_turer(self):
        self.assertEqual(sozlesme.SES_SURE_MIN_ORAN, agentic.VOICE_DURATION_MIN_RATIO)
        self.assertEqual(sozlesme.SES_SURE_MAX_ORAN, agentic.VOICE_DURATION_MAX_RATIO)

    def test_ffmpeg_clamp_sozlesmeden_turer(self):
        self.assertEqual(sozlesme.VIDEO_HIZ_MAKS, media.MAKS_VIDEO_HIZLANDIRMA)
        self.assertEqual(sozlesme.VIDEO_HIZ_MIN, media.MIN_VIDEO_YAVASLATMA)

    def test_dokuman_bandiyla_uyumlu(self):
        dokuman = (KOK / "schema/proje_yapisi.md").read_text(encoding="utf-8")
        self.assertIn(f"{sozlesme.SES_SURE_MIN_ORAN:g}–{sozlesme.SES_SURE_MAX_ORAN:g}", dokuman)


if __name__ == "__main__":
    unittest.main()
