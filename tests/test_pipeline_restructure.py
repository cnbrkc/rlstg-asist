"""Yeniden yapılandırma regresyon testleri.

Korunan davranışlar:
  * _qa_regeneration_loop 15'lü döndürme sıralaması ve yalnızca DUO_SCRIPT_FAIL
    işaretinde geçerli DUO TTS varsa render'ı boğmayan duo_nonblocking_fallback.
  * Research tamamen düşerse yalnızca OBSERVED gerçeklerle Fact Lock fallback'i.
  * Caption/Threads sosyal koruması: artifact/boş çıktı reddi + Fact Lock fallback'i.
  * Agentic reels_state'in turkiye_ilgi_kancasi alanını Fact Lock'tan doldurması.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.pipeline import (
    _caption_calistir,
    _qa_calistir,
    _qa_regeneration_loop,
    _qa_sonucunu_coz,
    _research_calistir,
    _solo_duo_qa_korumasini_uygula,
    _threads_calistir,
    _threads_state_normalize,
)
from core.agentic import agentic_icerik_uretimi
from core.media import gecici_ses_yolu


class _FakeRouter:
    def __init__(self, value, raise_exc=None):
        self.value = value
        self.raise_exc = raise_exc
        self.calls = 0

    def metin_uret(self, content, prompt, schema, log, arama_kullan=False, **_):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return self.value, "fake-model"


def _make_wav(path):
    Path(path).write_bytes(b"RIFF-fake-wav")
    return str(path)


class _AgenticRouter:
    """Agentic döngüdeki ajanları sırayla yanıtlar."""

    def __init__(self):
        self.calls = 0

    def metin_uret(self, content, prompt, schema, log, arama_kullan=False, **_):
        self.calls += 1
        props = schema.get("properties", {})
        if "kronik_sikayetler" in props:
            return {"kronik_sikayetler": [], "turkiye_ozel_magduriyet": "", "viral_kan_mali": ""}, "m"
        if "kapak_basliklari" in props:
            return {
                "secilen_sablon": "Ters_Kose",
                "kapak_basliklari": [
                    {"ust": "BU SUV NORMAL DEĞİL", "alt": "Aile arabası gibi duruyor ama değil"},
                    {"ust": "GERÇEK RAKAM ŞOK", "alt": "Kağıt üzerindeki rakam gerçeği anlatmıyor"},
                    {"ust": "KİMSE BEKLEMİYORDU", "alt": "Sonuna kadar izleyince mesele netleşiyor"},
                    {"ust": "ALMADAN ÖNCE İZLE", "alt": "Bu kararı vermeden önce bunu bil"},
                    {"ust": "DETAY FARKI BÜYÜK", "alt": "Kullanıcıların şikayet ettiği nokta bu"},
                ],
                "kapak_metni": "BU SUV NORMAL DEĞİL",
                "ilk_3_saniye_kanca": "Bu fiyat herkesi şaşırttı",
            }, "m"
        if "segments" in props:
            return {
                "segments": [
                    {"speaker": "female", "tts_tag": "", "text": "Bu fiyat gerçek mi?"},
                    {"speaker": "male", "tts_tag": "", "text": "Türkiye fiyatı değil."},
                ],
                "yorum_tetikleyici_soru": "Siz alır mıydınız?",
            }, "m"
        if "score" in props:
            return {"score": 8, "approved": True, "feedback": ""}, "m"
        return {"reels_baslik": "T", "reels_aciklama": "açıklama", "reels_hashtag": ["#oto"]}, "m"

    def ses_uret(self, text, voice, output, log, hiz_carpani=1.0, **kwargs):
        _make_wav(output)
        return True, "fake-tts"


def _mock_duo_ses(router, segments, output_path, log, hiz_carpani=1.0):
    _make_wav(output_path)
    return True, "fake-duo-tts"


class QaRegenerationLoopTests(unittest.TestCase):
    def _run_loop(self, qa_result, mode="DUO"):
        wav = _make_wav(gecici_ses_yolu())
        qa_results = qa_result if isinstance(qa_result, list) else [qa_result]
        qa_index = {"value": 0}
        speakers = [{"speaker": "female", "text": "a"}, {"speaker": "male", "text": "b"}]
        if mode == "SOLO_FEMALE":
            speakers = [{"speaker": "female", "text": "a"}, {"speaker": "female", "text": "b"}]
        elif mode == "SOLO_MALE":
            speakers = [{"speaker": "male", "text": "a"}, {"speaker": "male", "text": "b"}]
        agentic_ret = (
            {"seslendirme_metni": "metin", "kapak_basliklari": [], "turkiye_ilgi_kancasi": "kanca"},
            "m-reels",
            {"mode": mode},
            {"status": "ready", "segments": speakers, "contract": {"mode": mode}},
            True,
            "m-ses",
            mode,
            wav,
            {"reels_aciklama": "caption", "reels_hashtag": ["#oto"]},
        )

        def _qa_response(*_args, **_kwargs):
            index = min(qa_index["value"], len(qa_results) - 1)
            qa_index["value"] += 1
            return qa_results[index], "m-qa"

        with patch("core.pipeline.agentic_icerik_uretimi", return_value=agentic_ret), \
             patch("core.pipeline._qa_calistir", side_effect=_qa_response), \
             patch("core.pipeline._threads_calistir", return_value=({"threads_aciklamasi": "t"}, "m-threads")):
            router = _FakeRouter({})
            logs = []
            result = _qa_regeneration_loop(
                router, {}, {}, {}, {}, {}, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append,
            )
        if os.path.exists(wav):
            os.remove(wav)
        return result

    def test_return_order_contract(self):
        r = self._run_loop({"overall": "PASS", "regeneration_targets": []})
        self.assertEqual(len(r), 15)
        reels, model_reels, duo_plan, duo_script, ses_ok, ses_model, ses_modu, ses_dosyasi, caption, threads, qa_state, qa_rounds, model_caption, model_threads, qa_pass = r
        self.assertIn("seslendirme_metni", reels)
        self.assertEqual(model_reels, "m-reels")
        self.assertEqual(duo_plan["mode"], "DUO")
        self.assertEqual(duo_script["status"], "ready")
        self.assertTrue(ses_ok)
        self.assertEqual(ses_modu, "DUO")
        self.assertEqual(caption["reels_aciklamasi"], "caption")
        self.assertEqual(threads["threads_aciklamasi"], "t")
        self.assertEqual(qa_rounds, 0)
        self.assertEqual(model_threads, "m-threads")
        self.assertTrue(qa_pass)

    def test_duo_script_only_fail_is_nonblocking_with_valid_duo_tts(self):
        r = self._run_loop({"overall": "FAIL", "regeneration_targets": ["DUO_SCRIPT_FAIL"]})
        qa_state, qa_pass = r[10], r[14]
        self.assertTrue(qa_pass)
        self.assertTrue(qa_state.get("duo_nonblocking_fallback"))
        self.assertEqual(qa_state["overall"], "PASS")
        self.assertEqual(qa_state["regeneration_targets"], [])

    def test_other_targets_remain_blocking(self):
        r = self._run_loop({"overall": "FAIL", "regeneration_targets": ["DUO_SCRIPT_FAIL", "VOICEOVER_FAIL"]})
        self.assertFalse(r[14])

    def test_no_targets_fail_stays_failing(self):
        r = self._run_loop({"overall": "FAIL", "regeneration_targets": []})
        self.assertFalse(r[14])

    def test_solo_mode_ignores_unsubstantiated_duo_target_after_caption_regen(self):
        qa_caption_fail = {
            "overall": "FAIL",
            "regeneration_targets": ["CAPTION_FAIL"],
            "caption_check": "FAIL: açıklama yeniden yazılmalı",
        }
        # CI logundaki durum: model yalnız DUO_SCRIPT_FAIL dedi, duo_check dahil
        # herhangi bir kontrol alanında FAIL açıklaması döndürmedi.
        qa_false_duo_fail = {"overall": "FAIL", "regeneration_targets": ["DUO_SCRIPT_FAIL"]}
        result = self._run_loop([qa_caption_fail, qa_false_duo_fail], mode="SOLO_FEMALE")

        self.assertTrue(result[14], "SOLO moddaki temelsiz DUO etiketi geçerli TTS render'ını durdurmamalı")
        self.assertEqual(result[11], 1)
        self.assertEqual(result[10]["overall"], "PASS")
        self.assertEqual(result[10]["ignored_qa_targets"], ["DUO_SCRIPT_FAIL"])
        self.assertEqual(result[10]["regeneration_targets"], [])

    def test_solo_mode_ignores_explicit_failure_for_missing_duo_only_features(self):
        qa_result = {
            "overall": "FAIL",
            "regeneration_targets": ["DUO_SCRIPT_FAIL"],
            "duo_check": "FAIL: no second speaker is present, no friction, reactions, and back-and-forth",
        }
        result = self._run_loop(qa_result, mode="SOLO_FEMALE")

        self.assertTrue(result[14], "Eksik duo özellikleri SOLO modda render'ı engellememeli")
        self.assertIn("duo_check", result[10]["ignored_qa_checks"])

    def test_solo_mode_explicit_dialogue_shaped_failure_is_not_silenced(self):
        qa_result = {
            "overall": "FAIL",
            "regeneration_targets": ["DUO_SCRIPT_FAIL"],
            "duo_check": "FAIL: metin hâlâ soru-cevap kalıbında",
        }
        result = self._run_loop(qa_result, mode="SOLO_FEMALE")

        self.assertFalse(result[14], "Açık script FAIL'i SOLO modda da render'ı durdurmalı")


class SoloDuoQaGuardTests(unittest.TestCase):
    def test_concrete_voiceover_fact_and_caption_failures_remain_blocking(self):
        failures = {
            "tts_check": ("FAIL: seslendirme cümlesi kesilmiş", "VOICEOVER_FAIL"),
            "fact_check": ("FAIL: doğrulanmamış iddia", "VOICEOVER_FAIL"),
            "caption_check": ("FAIL: açıklama yanlış bilgiyi tekrar ediyor", "CAPTION_FAIL"),
        }
        for check, (message, expected_target) in failures.items():
            with self.subTest(check=check):
                qa_state = {
                    "overall": "FAIL",
                    "regeneration_targets": ["DUO_SCRIPT_FAIL"],
                    check: message,
                }
                qa_state = _solo_duo_qa_korumasini_uygula(qa_state, "SOLO_FEMALE", lambda *_: None)
                overall, targets, _ = _qa_sonucunu_coz(qa_state, {}, 30, lambda *_: None)

                self.assertEqual(overall, "FAIL")
                self.assertIn(expected_target, targets)


class ResearchFallbackTests(unittest.TestCase):
    def test_observed_facts_fallback_when_research_fails(self):
        video_state = {
            "video_identity": {"brand": "BYD", "exact_model": "Great Tang"},
            "observed_facts": ["650 km menzil yazısı ekranda görülüyor"],
        }
        router = _FakeRouter({}, raise_exc=RuntimeError("503 UNAVAILABLE"))
        with patch("core.pipeline.web_arastirma_yap", return_value=""), \
             patch("core.pipeline.otv_ek_arastirma") as ek_arastirma:
            state, model = _research_calistir(router, video_state, lambda m: None)
        ek_arastirma.assert_not_called()
        self.assertEqual(model, "forensic-fallback")
        self.assertTrue(any(f["fact"].startswith("Videoda tanımlanan araç: BYD Great Tang") for f in state["facts"]))
        gozlenen = [f for f in state["facts"] if not f.get("_otv_kilidi")]
        self.assertTrue(gozlenen)
        self.assertTrue(all(f["status"] == "OBSERVED" for f in gozlenen))
        self.assertEqual(state["vergi_kilidi"]["durum"], "YASAK")
        self.assertEqual(state["turkiye_satis_durumu"], "BILINMIYOR")


class SocialGuardTests(unittest.TestCase):
    def setUp(self):
        self.fact = {"facts": [{"fact": "Çin'de 119.900 Yuan.", "status": "VERIFIED"}]}
        self.editorial = {"core_story": "Fiyat şoku"}
        self.video = {"video_identity": {"brand": "BYD", "exact_model": "Great Tang"}}

    def test_caption_artifact_rejected_then_fact_lock_fallback(self):
        router = _FakeRouter({"reels_aciklamasi": "/tmp/ses_x.wav", "reels_hashtagleri": ["#x"]})
        state, model = _caption_calistir(router, {}, self.fact, self.editorial, self.video, lambda m: None, "dengeli")
        self.assertEqual(model, "local-fallback")
        self.assertNotIn(".wav", state["reels_aciklamasi"])
        self.assertTrue(state["reels_hashtagleri"])
        self.assertEqual(router.calls, 2)  # 1 kontrollü yeniden üretim

    def test_caption_valid_output_passes_through(self):
        router = _FakeRouter({"reels_aciklamasi": "Gerçek caption.", "reels_hashtagleri": ["#a", "#b"]})
        state, model = _caption_calistir(router, {}, self.fact, self.editorial, self.video, lambda m: None, "dengeli")
        self.assertEqual(model, "fake-model")
        self.assertEqual(state["reels_aciklamasi"], "Gerçek caption.")
        self.assertEqual(router.calls, 1)

    def test_threads_empty_output_gets_fact_lock_fallback(self):
        router = _FakeRouter({"threads_aciklamasi": ""})
        state, model = _threads_calistir(router, self.video, self.fact, self.editorial, lambda m: None, "dengeli")
        self.assertEqual(model, "local-fallback")
        self.assertTrue(state["threads_aciklamasi"])

    def test_threads_text_is_capped_at_500_characters_at_sentence_boundary(self):
        sentence = "Otomobilde fiyat algısı kadar gerçek kullanım deneyimi de belirleyici."
        text = (sentence + " ") * 5 + ("İkinci görüşü genişleten ek açıklama. " * 20)

        normalized = _threads_state_normalize({"threads_aciklamasi": text})["threads_aciklamasi"]

        self.assertLessEqual(len(normalized), 500)
        self.assertTrue(normalized.endswith("."))

    def test_threads_text_without_sentence_boundary_is_cut_at_word_boundary(self):
        text = "kelime " * 120
        normalized = _threads_state_normalize(text)["threads_aciklamasi"]

        self.assertLessEqual(len(normalized), 500)
        self.assertFalse(normalized.endswith("kelim"))

    def test_qa_receives_the_same_capped_threads_text_that_will_be_delivered(self):
        class _CaptureRouter:
            content = ""

            def metin_uret(self, content, _prompt, _schema, _log, **_kwargs):
                self.content = content
                return {"overall": "PASS", "regeneration_targets": []}, "fake-qa"

        raw_threads = ("Net görüş burada. " * 40) + ("Devam eden açıklama. " * 15)
        normalized = _threads_state_normalize({"threads_aciklamasi": raw_threads})
        router = _CaptureRouter()

        _qa_calistir(
            router, {}, {}, {}, {}, {},
            {"threads_aciklamasi": raw_threads},
            30, lambda *_: None, ton="dengeli",
        )

        self.assertIn("### THREADS\n" + repr(normalized), router.content)
        self.assertNotIn(raw_threads, router.content)
        self.assertLessEqual(len(normalized["threads_aciklamasi"]), 500)


class TurkiyeKancaTests(unittest.TestCase):
    @patch("core.agentic._reels_kelime_ayarlarini_hazirla", return_value=(10, 1, 999, 2.5, 5))
    @patch("core.agentic._ses_suresini_al", return_value=25.0)
    @patch("core.agentic.duo_ses_uret", side_effect=_mock_duo_ses)
    def test_reels_state_carries_top_turkey_signal(self, mock_tts, mock_ses, mock_kelime):
        fact_state = {
            "turkiye_ilgi_sinyalleri": [
                {"kategori": "fiyat_deger", "bulgu": "Çin'de 119.900 Yuan", "guvenli_anlatim": "Çin pazarında 119.900 Yuan ile konumlanıyor.", "onem_puani": 9},
                {"kategori": "tasarim", "bulgu": "27 inç ekran", "guvenli_anlatim": "27 inç ekran görülüyor.", "onem_puani": 4},
            ]
        }
        router = _AgenticRouter()
        reels, *_ = agentic_icerik_uretimi(
            router, {}, fact_state, {}, 30, "dengeli", "Autonoe", lambda m: None, mod_karari={"mode": "DUO"}
        )
        self.assertEqual(reels["turkiye_ilgi_kancasi"], "Çin pazarında 119.900 Yuan ile konumlanıyor.")


if __name__ == "__main__":
    unittest.main()
