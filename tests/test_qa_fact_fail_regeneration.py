"""Eylül 2026 üretim durması (FAIL_NO_VIDEO) regresyon testleri.

Kök neden: Final QA `regeneration_targets=["FACT_FAIL"]` döndürdü. Prompt bu
hedefi açıkça tanımlıyor ama loop yalnız 5 hedefi tanıyordu; FACT_FAIL
"desteklenmeyen" sayıldı, HİÇ yenileme yapılmadan ve nedeni loglanmadan render
durduruldu (9 dk üretim → video yok).
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.pipeline import _qa_regeneration_loop, _qa_sonucunu_coz
from core.media import gecici_ses_yolu


def _make_wav():
    path = gecici_ses_yolu()
    Path(path).write_bytes(b"RIFF-fake-wav")
    return path


def _agentic_ret(wav, text="metin"):
    return (
        {"seslendirme_metni": text, "kapak_basliklari": [{"ust": "A B", "alt": "c d e f"}], "turkiye_ilgi_kancasi": "k"},
        "m-reels",
        {"mode": "DUO"},
        {"status": "ready", "segments": [{"speaker": "female", "text": "a"}, {"speaker": "male", "text": "b"}], "contract": {"mode": "DUO"}},
        True,
        "m-ses",
        "DUO",
        wav,
        {"reels_aciklama": "caption", "reels_hashtag": ["#oto"]},
    )


class _Router:
    def metin_uret(self, *a, **k):
        return {}, "fake"


class QaFactFailTests(unittest.TestCase):
    def _run(self, qa_results, agentic_side=None, extra_patches=()):
        wav = _make_wav()
        calls = []

        def fake_agentic(*args, **kwargs):
            calls.append(kwargs)
            return _agentic_ret(wav)

        qa_iter = iter(qa_results)
        logs = []
        patches = [
            patch("core.pipeline.agentic_icerik_uretimi", side_effect=agentic_side or fake_agentic),
            patch("core.pipeline._qa_calistir", side_effect=lambda *a, **k: (dict(next(qa_iter)), "m-qa")),
            patch("core.pipeline._threads_calistir", return_value=({"threads_aciklamasi": "t"}, "m-threads")),
        ] + list(extra_patches)
        for p in patches:
            p.start()
        try:
            result = _qa_regeneration_loop(
                _Router(), {}, {}, {}, {}, {}, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append,
            )
        finally:
            for p in patches:
                p.stop()
            if os.path.exists(wav):
                os.remove(wav)
        return result, calls, logs

    def test_fact_fail_triggers_guided_regeneration_and_passes(self):
        qa_fail = {
            "overall": "FAIL",
            "regeneration_targets": ["FACT_FAIL"],
            "fact_check": "FAIL: Seslendirmede Fact Lock'ta olmayan 250 beygir iddiası var.",
        }
        qa_pass = {"overall": "PASS", "regeneration_targets": []}
        result, calls, logs = self._run([qa_fail, qa_pass])
        self.assertTrue(result[14], "FACT_FAIL sonrası yenileme + PASS ile render devam etmeli")
        self.assertEqual(result[11], 1)
        self.assertEqual(len(calls), 2)
        # 2. çağrı QA gerekçesini Script Writer'a taşır ve 1. turun bağlamını kullanır.
        self.assertIn("250 beygir", calls[1]["qa_geri_bildirimi"])
        self.assertIs(calls[0]["baglam"], calls[1]["baglam"])
        # Hangi kontrolün FAIL verdiği loglanır; model metni (gerekçe) loga yazılmaz.
        self.assertTrue(any("QA FAIL kontrolleri: fact_check" in line for line in logs))
        self.assertFalse(any("250 beygir" in line for line in logs))

    def test_fact_fail_still_failing_after_regen_blocks_render(self):
        qa_fail = {"overall": "FAIL", "regeneration_targets": ["FACT_FAIL"], "fact_check": "FAIL: seslendirmede uydurma rakam"}
        result, calls, _ = self._run([qa_fail, qa_fail])
        self.assertFalse(result[14])
        self.assertEqual(len(calls), 2)

    def test_social_only_leftover_gets_extra_round_then_nonblocking(self):
        qa_fail = {"overall": "FAIL", "regeneration_targets": ["CAPTION_FAIL", "THREADS_FAIL"],
                   "caption_check": "FAIL: 600 karakter altında"}
        with patch("core.pipeline._caption_calistir", return_value=({"reels_aciklamasi": "c2", "reels_hashtagleri": ["#a"]}, "m")) as cap:
            result, calls, logs = self._run([qa_fail, qa_fail, qa_fail])
        qa_state, qa_pass = result[10], result[14]
        self.assertTrue(qa_pass)
        self.assertEqual(result[11], 2, "sosyal katman için ek bir düzeltme turu yapılır")
        self.assertEqual(cap.call_count, 2)
        self.assertIn("600 karakter", cap.call_args.kwargs["qa_geri_bildirimi"])
        self.assertEqual(qa_state["nonblocking_targets"], ["CAPTION_FAIL", "THREADS_FAIL"])
        self.assertTrue(qa_state["social_nonblocking_fallback"])
        self.assertEqual(len(calls), 1, "caption/threads için seslendirme yeniden üretilmez")
        self.assertTrue(any("ek bir düzeltme turu" in line for line in logs))

    def test_social_extra_round_can_fix_and_pass(self):
        qa_fail = {"overall": "FAIL", "regeneration_targets": ["CAPTION_FAIL"], "caption_check": "FAIL: hashtag eksik"}
        qa_ok = {"overall": "PASS", "regeneration_targets": []}
        with patch("core.pipeline._caption_calistir", return_value=({"reels_aciklamasi": "c2", "reels_hashtagleri": ["#a"]}, "m")):
            result, calls, _ = self._run([qa_fail, qa_fail, qa_ok])
        self.assertTrue(result[14])
        self.assertNotIn("nonblocking_targets", result[10])

    def test_voice_regen_after_voice_fail_is_not_given_extra_round(self):
        qa_fail = {"overall": "FAIL", "regeneration_targets": ["VOICEOVER_FAIL"], "tone_check": "FAIL: robotik"}
        result, calls, _ = self._run([qa_fail, qa_fail])
        self.assertFalse(result[14])
        self.assertEqual(len(calls), 2)

    def test_fact_fail_regenerates_hook_too(self):
        qa_fail = {"overall": "FAIL", "regeneration_targets": ["FACT_FAIL"], "fact_check": "FAIL: seslendirmede uydurma rakam"}
        qa_ok = {"overall": "PASS", "regeneration_targets": []}
        hook_flags = []

        def fake_agentic(*args, **kwargs):
            hook_flags.append(bool(kwargs.get("baglam", {}).get("hook_yenile")))
            return _agentic_ret(self._wav)

        self._wav = _make_wav()
        result, calls, _ = self._run([qa_fail, qa_ok], agentic_side=fake_agentic)
        self.assertTrue(result[14])
        self.assertEqual(hook_flags, [False, True], "gerçeklik hatasında Hook da QA geri bildirimiyle yenilenir")

    def test_tone_fail_reuses_hook(self):
        qa_fail = {"overall": "FAIL", "regeneration_targets": ["VOICEOVER_FAIL"], "tone_check": "FAIL: robotik"}
        qa_ok = {"overall": "PASS", "regeneration_targets": []}
        hook_flags = []

        def fake_agentic(*args, **kwargs):
            hook_flags.append(bool(kwargs.get("baglam", {}).get("hook_yenile")))
            return _agentic_ret(self._wav)

        self._wav = _make_wav()
        self._run([qa_fail, qa_ok], agentic_side=fake_agentic)
        self.assertEqual(hook_flags, [False, False])

    def test_hook_fail_regenerates_hook(self):
        qa_fail = {"overall": "FAIL", "regeneration_targets": ["HOOK_FAIL"], "hook_check": "FAIL: ilk 3 saniye zayıf"}
        qa_ok = {"overall": "PASS", "regeneration_targets": []}
        hook_flags = []

        def fake_agentic(*args, **kwargs):
            hook_flags.append(bool(kwargs.get("baglam", {}).get("hook_yenile")))
            return _agentic_ret(self._wav)

        self._wav = _make_wav()
        self._run([qa_fail, qa_ok], agentic_side=fake_agentic)
        self.assertEqual(hook_flags, [False, True])

    def test_caption_fail_with_voice_regen_uses_caption_agent_with_feedback(self):
        qa_fail = {"overall": "FAIL", "regeneration_targets": ["VOICEOVER_FAIL", "CAPTION_FAIL"],
                   "tone_check": "FAIL: robotik", "caption_check": "FAIL: çok kısa"}
        qa_ok = {"overall": "PASS", "regeneration_targets": []}
        with patch("core.pipeline._caption_calistir", return_value=({"reels_aciklamasi": "yeni caption", "reels_hashtagleri": ["#a"]}, "m-cap")) as cap:
            result, calls, _ = self._run([qa_fail, qa_ok])
        self.assertTrue(result[14])
        cap.assert_called_once()
        self.assertIn("çok kısa", cap.call_args.kwargs["qa_geri_bildirimi"])
        self.assertEqual(result[8]["reels_aciklamasi"], "yeni caption")

    def test_failed_caption_regen_keeps_previous_model_caption(self):
        qa_fail = {"overall": "FAIL", "regeneration_targets": ["CAPTION_FAIL"], "caption_check": "FAIL: kısa"}
        qa_ok = {"overall": "PASS", "regeneration_targets": []}
        with patch("core.pipeline._caption_calistir", return_value=({"reels_aciklamasi": "şablon", "reels_hashtagleri": ["#a"]}, "local-fallback")):
            result, calls, _ = self._run([qa_fail, qa_ok])
        self.assertEqual(result[8]["reels_aciklamasi"], "caption", "model caption'ı şablonla ezilmez")

    def test_cover_only_regenerates_hook_without_new_tts(self):
        qa_fail = {"overall": "FAIL", "regeneration_targets": ["COVER_FAIL"], "cover_check": "FAIL: kapak ilk cümleyle aynı"}
        qa_pass = {"overall": "PASS", "regeneration_targets": []}
        yeni_reels = {"seslendirme_metni": "metin", "kapak_basliklari": [{"ust": "YENİ KAPAK", "alt": "Yeni alt başlık burada duruyor"}]}
        with patch("core.pipeline.kapaklari_yeniden_uret", return_value=yeni_reels) as kapak:
            result, calls, _ = self._run([qa_fail, qa_pass])
        self.assertTrue(result[14])
        self.assertEqual(len(calls), 1, "COVER_FAIL tek başına Script+TTS yeniden üretmez")
        kapak.assert_called_once()
        self.assertIn("kapak ilk cümleyle aynı", kapak.call_args.kwargs["qa_geri_bildirimi"])
        self.assertEqual(result[0]["kapak_basliklari"][0]["ust"], "YENİ KAPAK")


class LengthOverrideTests(unittest.TestCase):
    def test_length_override_requires_strict_range(self):
        from core.pipeline import _uzunluk_uygun_mu
        # 30 sn → hedef 85, sıkı aralık 76-94.
        self.assertTrue(_uzunluk_uygun_mu({"seslendirme_metni": "kelime " * 85}, 30))
        # ±%20 toleransta ama sıkı aralık dışında → QA itirazı bastırılmaz.
        self.assertFalse(_uzunluk_uygun_mu({"seslendirme_metni": "kelime " * 100}, 30))

    def test_length_override_checks_real_tts_duration(self):
        from core.pipeline import _uzunluk_uygun_mu
        wav = _make_wav()
        try:
            with patch("core.agentic._ses_suresini_al", return_value=40.0):
                self.assertFalse(_uzunluk_uygun_mu({"seslendirme_metni": "kelime " * 85}, 30, wav))
            with patch("core.agentic._ses_suresini_al", return_value=30.0):
                self.assertTrue(_uzunluk_uygun_mu({"seslendirme_metni": "kelime " * 85}, 30, wav))
        finally:
            os.remove(wav)


class QaSonucCozTests(unittest.TestCase):
    def _coz(self, qa, text="kelime " * 90, sure=30):
        return _qa_sonucunu_coz(dict(qa), {"seslendirme_metni": text}, sure, lambda m: None)

    def test_overall_with_free_text_is_parsed(self):
        overall, targets, _ = self._coz({"overall": "PASS: tüm kontroller geçti", "regeneration_targets": []})
        self.assertEqual(overall, "PASS")
        self.assertEqual(targets, [])

    def test_aliases_are_mapped(self):
        _, targets, _ = self._coz({"overall": "FAIL", "regeneration_targets": ["hashtag_fail", "TONE_FAIL", "visual_match_check"]})
        self.assertEqual(targets, ["VOICEOVER_FAIL", "COVER_FAIL", "CAPTION_FAIL"])

    def test_targets_derived_from_checks_when_missing(self):
        _, targets, failing = self._coz({
            "overall": "FAIL", "regeneration_targets": [],
            "threads_check": "FAIL: 280 karakter altında", "hook_check": "PASS: güçlü",
        })
        self.assertEqual(targets, ["THREADS_FAIL"])
        self.assertIn("threads_check", failing)

    def test_fact_scope_caption_only(self):
        _, targets, _ = self._coz({
            "overall": "FAIL", "regeneration_targets": ["FACT_FAIL"],
            "fact_check": "FAIL: caption içinde doğrulanmamış fiyat var",
        })
        self.assertEqual(targets, ["CAPTION_FAIL"])

    def test_placeholder_targets_ignored(self):
        overall, targets, _ = self._coz({"overall": "FAIL", "regeneration_targets": ["NONE"]})
        self.assertEqual(targets, [])
        self.assertEqual(overall, "FAIL")

    def test_wrong_length_check_is_overridden(self):
        # sure=30 → hedef 85 kelime (2.9 kelime/sn); 88 kelime tolerans içinde.
        overall, targets, failing = self._coz({
            "overall": "FAIL", "regeneration_targets": ["VOICEOVER_FAIL"],
            "length_check": "FAIL: kelime sayısı aralık dışında", "tts_check": "PASS",
        }, text="kelime " * 88)
        self.assertEqual(targets, [])
        self.assertEqual(overall, "PASS")
        self.assertNotIn("length_check", failing)

    def test_real_length_problem_not_overridden(self):
        _, targets, _ = self._coz({
            "overall": "FAIL", "regeneration_targets": ["VOICEOVER_FAIL"],
            "length_check": "FAIL: çok kısa",
        }, text="kelime " * 20)
        self.assertEqual(targets, ["VOICEOVER_FAIL"])


if __name__ == "__main__":
    unittest.main()
