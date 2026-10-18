"""TTS sahne yönergesi konuşulan metne sızmamalı; vurgu teslimat notunda kalmalı."""
import io
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.config import SES_LEGACY_MODELLERI, SES_MODELLERI
from core.router import SmartRouter
from core.tts_delivery import replik_tts_hazirla, teslimat_blogu, transkripti_ayikla


def _audio_response(data=b"pcm"):
    part = type("Part", (), {"inline_data": type("Inline", (), {"data": data})()})()
    content = type("Content", (), {"parts": [part]})()
    candidate = type("Candidate", (), {"content": content})()
    return type("Response", (), {"candidates": [candidate]})()


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

        response = _audio_response()

        def fake_request(models, contents, config, log):
            captured["prompt"] = contents
            return response, "key+tts"

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

    def test_gemini_38_single_speaker_uses_turn_level_speech_metadata(self):
        router = SmartRouter.__new__(SmartRouter)
        captured = {}

        response = _audio_response()

        def fake_request(models, contents, config, log):
            captured.update(models=models, contents=contents, config=config)
            return response, "key+gemini-3.8-flash-tts"

        router._make_request = fake_request
        router._tts_kaydet = lambda audio, path, speed, log: audio == b"pcm"
        router.istek_profili = lambda *a, **k: _noop()
        ok, info = router.ses_uret(
            "Bu fiyat gerçek mi?", "Autonoe", "/tmp/not-written.wav", lambda *_: None,
            segments=[{"speaker": "female", "text": "Bu fiyat gerçek mi?", "style": "curious"}],
        )

        self.assertTrue(ok)
        self.assertIn("gemini-3.8-flash-tts", info)
        self.assertEqual(captured["models"], SES_MODELLERI)
        self.assertIsInstance(captured["contents"], list)
        wire = captured["contents"][0].model_dump(exclude_none=True, by_alias=True)
        part = wire["parts"][0]
        self.assertEqual(part["text"], "Bu fiyat gerçek mi?")
        self.assertIn("Natural, conversational Turkish narration", part["speechMetadata"]["style"])
        self.assertIn("curious", part["speechMetadata"]["style"])
        self.assertNotIn("speaker", part["speechMetadata"])

    def test_single_speaker_without_segments_keeps_legacy_prompt_contract(self):
        router = SmartRouter.__new__(SmartRouter)
        captured = {}
        response = _audio_response()

        def fake_request(models, contents, config, log):
            captured.update(models=models, contents=contents, config=config)
            return response, "key+legacy-tts"

        router._make_request = fake_request
        router._tts_kaydet = lambda audio, path, speed, log: audio == b"pcm"
        router.istek_profili = lambda *a, **k: _noop()
        ok, info = router.ses_uret(
            "Merhaba.", "Autonoe", "/tmp/not-written.wav", lambda *_: None,
        )

        self.assertTrue(ok)
        self.assertIn("legacy-tts", info)
        self.assertEqual(captured["models"], SES_LEGACY_MODELLERI)
        self.assertIsInstance(captured["contents"], str)
        self.assertIn("#### TRANSCRIPT\nMerhaba.", captured["contents"])
        self.assertEqual(
            captured["config"].speech_config.voice_config.prebuilt_voice_config.voice_name,
            "Autonoe",
        )

    def test_gemini_38_duo_turns_use_structured_speaker_metadata(self):
        router = SmartRouter.__new__(SmartRouter)
        captured = {}

        response = _audio_response()

        def fake_request(models, contents, config, log):
            captured.update(models=models, contents=contents, config=config)
            return response, "key+gemini-3.8-flash-tts"

        router._make_request = fake_request
        router._tts_kaydet = lambda audio, path, speed, log: audio == b"pcm"
        router.istek_profili = lambda *a, **k: _noop()
        ok, _ = router.coklu_ses_uret(
            "Autonoe: Merhaba.\nCharon: Selam.",
            [("Autonoe", "Autonoe"), ("Charon", "Charon")],
            "/tmp/not-written.wav", lambda *_: None,
            segments=[
                {"speaker": "Autonoe", "text": "Merhaba.", "style": "curious"},
                {"speaker": "Charon", "text": "Selam.", "style": "calm"},
            ],
        )

        self.assertTrue(ok)
        self.assertEqual(captured["models"], SES_MODELLERI)
        self.assertIsInstance(captured["contents"], list)
        wire = captured["contents"][0].model_dump(exclude_none=True, by_alias=True)
        parts = wire["parts"]
        self.assertEqual([part["text"] for part in parts], ["Merhaba.", "Selam."])
        self.assertEqual(
            [part["speechMetadata"]["speaker"] for part in parts],
            ["Autonoe", "Charon"],
        )
        self.assertTrue(all("Natural, conversational Turkish dialogue" in part["speechMetadata"]["style"] for part in parts))
        self.assertTrue(all("verbatim" in part["speechMetadata"]["style"] for part in parts))
        self.assertIn("curious", parts[0]["speechMetadata"]["style"])
        self.assertIn("calm", parts[1]["speechMetadata"]["style"])
        speech_config = captured["config"].speech_config
        self.assertEqual(
            [item.speaker for item in speech_config.multi_speaker_voice_config.speaker_voice_configs],
            ["Autonoe", "Charon"],
        )

    def test_gemini_38_failure_uses_legacy_tts_fallback_without_changing_modern_first(self):
        router = SmartRouter.__new__(SmartRouter)
        attempts = []

        response = _audio_response()

        def fake_request(models, contents, config, log):
            attempts.append((models, contents))
            if len(attempts) == 1:
                raise RuntimeError("new speech_metadata schema unavailable")
            return response, "key+legacy-tts"

        router._make_request = fake_request
        router._tts_kaydet = lambda audio, path, speed, log: audio == b"pcm"
        router.istek_profili = lambda *a, **k: _noop()
        ok, info = router.ses_uret(
            "Merhaba.", "Autonoe", "/tmp/not-written.wav", lambda *_: None,
            segments=[{"speaker": "female", "text": "Merhaba."}],
        )

        self.assertTrue(ok)
        self.assertIn("legacy-tts", info)
        self.assertEqual(attempts[0][0], SES_MODELLERI)
        self.assertIsInstance(attempts[0][1], list)
        self.assertEqual(attempts[1][0], SES_LEGACY_MODELLERI)
        self.assertIsInstance(attempts[1][1], str)

    def test_complete_wav_is_written_as_is_and_raw_pcm_keeps_legacy_wrapper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "tts.wav"
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(bytes(16))
            complete_wav = buffer.getvalue()

            router = SmartRouter.__new__(SmartRouter)
            self.assertTrue(router._tts_kaydet(complete_wav, str(output), 1.0, lambda *_: None))
            self.assertEqual(output.read_bytes(), complete_wav)

            with patch("core.router.wav_yaz") as wav_yaz:
                self.assertTrue(router._tts_kaydet(b"pcm", str(output), 1.0, lambda *_: None))
            wav_yaz.assert_called_once_with(str(output), b"pcm")

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
