"""TTS sahne yönergesi konuşulan metne sızmamalı; vurgu teslimat notunda kalmalı."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.tts_delivery import replik_tts_hazirla, teslimat_blogu, transkripti_ayikla
from core.router import SmartRouter


class TtsDeliveryTests(unittest.TestCase):
    def test_bracket_tags_leave_spoken_text_and_become_style(self):
        konusma, stil = replik_tts_hazirla("[alaycı] Alman tekeli bitiyor", "[vurgulu]")
        self.assertEqual("Alman tekeli bitiyor", konusma)
        self.assertNotIn("alaycı", konusma.casefold())
        self.assertNotIn("vurgulu", konusma.casefold())
        self.assertIn("sarcasm", stil)
        self.assertIn("stress", stil)

    def test_real_sentence_starting_with_savunarak_is_spoken(self):
        konusma, stil = replik_tts_hazirla("Savunarak söylüyorum, vergi dilimi bu.", "")
        self.assertEqual("Savunarak söylüyorum, vergi dilimi bu.", konusma)
        self.assertEqual("", stil)

    def test_direction_prefix_is_not_spoken(self):
        konusma, stil = replik_tts_hazirla("Vurgulu: bu fiyat gerçek değil.", "")
        self.assertEqual("bu fiyat gerçek değil.", konusma)
        self.assertIn("stress", stil)

    def test_transcript_boundary_does_not_contain_direction_words(self):
        router = SmartRouter.__new__(SmartRouter)
        captured = {}

        class _Inline:
            data = b"pcm"

        class _Part:
            inline_data = _Inline()

        class _Content:
            parts = [_Part()]

        class _Candidate:
            content = _Content()

        class _Response:
            candidates = [_Candidate()]

        def fake_request(models, contents, config, log):
            captured["prompt"] = contents
            return _Response(), "key+tts"

        router._make_request = fake_request
        router._tts_kaydet = lambda audio, path, speed, log: True
        router.istek_profili = lambda *a, **k: _noop()

        ok, _ = router.coklu_ses_uret(
            "Autonoe: [vurgulu] Bu fiyat gerçek mi?\nCharon: [savunarak] Rakam bu.",
            [("Autonoe", "Autonoe"), ("Charon", "Charon")],
            "/tmp/not-written.wav",
            lambda *_: None,
        )
        self.assertTrue(ok)
        prompt = captured["prompt"]
        baslik = "#### TRANSCRIPT\n"
        self.assertIn(baslik, prompt)
        yon, transkript = prompt.rsplit(baslik, 1)
        self.assertIn("Bu fiyat gerçek mi?", transkript)
        self.assertIn("Rakam bu.", transkript)
        for yasak in ("vurgulu", "alaycı", "alayci", "savunarak", "[vurgulu]", "[savunarak]"):
            self.assertNotIn(yasak, transkript.casefold())
        self.assertIn("stress", yon.casefold())
        self.assertIn("defensive", yon.casefold())
        self.assertIn("Do not sing, chant, harmonize", prompt)

    def test_delivery_block_uses_style_not_turkish_tag(self):
        blok = teslimat_blogu([
            {"speaker": "female", "text": "Bu fiyat gerçek mi?", "tts_tag": "[şaşırarak]"},
        ])
        self.assertIn("surprise", blok)
        self.assertNotIn("şaşırarak", blok)
        temiz, notlar = transkripti_ayikla("Charon: [gülerek] Maalesef gerçek.")
        self.assertEqual("Charon: Maalesef gerçek.", temiz)
        self.assertIn("chuckle", notlar)
        self.assertNotIn("gülerek", temiz)

    def test_kisa_anahtar_kelime_icinde_gecerse_gercek_soz_silinmez(self):
        # "net" anahtarı "internette" kelimesinin içinde: substring eşleşme gerçek
        # sözü yönerge sanıp siliyordu; kelime sınırı eşleşmesi şart.
        konusma, stil = replik_tts_hazirla("[internette fiyatlar acayip] Bir de buna bak.", "")
        self.assertIn("internette fiyatlar acayip", konusma)
        self.assertIn("Bir de buna bak", konusma)
        self.assertNotIn("[", konusma)
        self.assertNotIn("]", konusma)
        self.assertNotIn("grounded and serious", stil)

    def test_bilinmeyen_tek_kelime_etiket_konusulmaz(self):
        # "[heyecanla]" tanınmayan etiketti: transkriptte kalıp TTS tarafından
        # yüksek sesle okunuyordu. Yönerge nota yazılır, konuşulmaz.
        konusma, stil = replik_tts_hazirla("[heyecanla] Harika bir gün.", "")
        self.assertEqual("Harika bir gün.", konusma)
        self.assertNotIn("heyecanla", konusma.casefold())
        self.assertTrue(stil)

    def test_rakamli_parantez_bilgisi_konusulur(self):
        konusma, _ = replik_tts_hazirla("[2024 model] Fiyat listesi çıktı.", "")
        self.assertIn("2024 model", konusma)
        self.assertIn("Fiyat listesi", konusma)
        self.assertNotIn("[", konusma)


class _noop:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


if __name__ == "__main__":
    unittest.main()
