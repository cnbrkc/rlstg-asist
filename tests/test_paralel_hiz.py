"""Üretim hız iyileştirmeleri (Eylül 2026 logu) regresyon testleri.

* agentic `_hizli_basarislik` router'da `hizli_basarisizlik` adıyla kaldığı için
  isteğe bağlı ajanlar hızlı-başarısızlık korumasını hiç almıyordu.
* Paralel kollar (Threads / Metadata / anlatım modu) için router bağlamı
  (istek profili + hızlı-başarısızlık) thread-local olmalı.
* QA yenilemesinde Detective/Hook yeniden çağrılmaz, Critic atlanır.
* Web sorguları paralel çalışır ama sonuç sırası korunur.
"""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.router import SmartRouter
from core.agentic import _hizli_basarislik, agentic_icerik_uretimi
from core.cover_titles import alt_baslik_duzenle, kapak_basliklarini_normalize_et, alt_kurala_uygun_mu
import core.web_search as web_search


def _mock_duo_ses(router, segments, output_path, log, hiz_carpani=1.0):
    Path(output_path).write_bytes(b"fake-duo-audio")
    return True, "fake-duo-tts"


class RouterContextTests(unittest.TestCase):
    def test_agentic_fast_fail_helper_reaches_router(self):
        router = SmartRouter()
        with _hizli_basarislik(router):
            self.assertEqual(router._overload_retry_off, 1)
        self.assertEqual(router._overload_retry_off, 0)

    def test_profile_and_fast_fail_are_thread_local(self):
        router = SmartRouter()
        seen = {}
        inside = threading.Event()
        release = threading.Event()

        def other_thread():
            inside.wait(2)
            seen["profil"] = router._aktif_profil
            seen["off"] = router._overload_retry_off
            release.set()

        t = threading.Thread(target=other_thread)
        t.start()
        with router.istek_profili("istege_bagli"), router.hizli_basarislik():
            inside.set()
            release.wait(2)
            self.assertEqual(router._aktif_profil, "istege_bagli")
        t.join(2)
        self.assertIsNone(seen["profil"])
        self.assertEqual(seen["off"], 0)

    def test_router_without_init_still_works(self):
        router = SmartRouter.__new__(SmartRouter)
        self.assertIsNone(router._aktif_profil)
        self.assertEqual(router._overload_retry_off, 0)


class _CountingRouter:
    def __init__(self):
        self.lock = threading.Lock()
        self.schemas = []
        self.prompts = []

    def metin_uret(self, content, prompt, schema, log, arama_kullan=False, **_):
        props = schema.get("properties", {})
        with self.lock:
            self.prompts.append(prompt)
        if "kronik_sikayetler" in props:
            name = "detective"
            value = {"kronik_sikayetler": ["x"], "turkiye_ozel_magduriyet": "", "viral_kan_mali": ""}
        elif "kapak_basliklari" in props:
            name = "hook"
            value = {
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
            }
        elif "segments" in props:
            name = "script"
            value = {"segments": [{"speaker": "female", "tts_tag": "", "text": "Bu fiyat gerçek mi?"},
                                  {"speaker": "male", "tts_tag": "", "text": "Türkiye fiyatı değil."}],
                     "yorum_tetikleyici_soru": "Siz alır mıydınız?"}
        elif "score" in props:
            name = "critic"
            value = {"score": 9, "approved": True, "feedback": ""}
        else:
            name = "metadata"
            value = {"reels_baslik": "T", "reels_aciklama": "açıklama", "reels_hashtag": ["#oto"]}
        with self.lock:
            self.schemas.append(name)
        return value, "m"

    def ses_uret(self, text, voice, output, log, hiz_carpani=1.0, **kwargs):
        Path(output).write_bytes(b"fake-solo-audio")
        return True, "fake-tts"


_kelime = patch("core.agentic._reels_kelime_ayarlarini_hazirla", return_value=(10, 1, 999, 2.5, 5))
_sure = patch("core.agentic._ses_suresini_al", return_value=25.0)
_duo = patch("core.agentic.duo_ses_uret", side_effect=_mock_duo_ses)


class AgenticReuseTests(unittest.TestCase):
    @_kelime
    @_sure
    @_duo
    def test_qa_regeneration_reuses_detective_and_hook_but_runs_critic_with_qa_context(self, *_):
        router = _CountingRouter()
        baglam = {}
        agentic_icerik_uretimi(router, {}, {}, {}, 30, "dengeli", "Autonoe", lambda m: None,
                               mod_karari={"mode": "DUO"}, baglam=baglam)
        self.assertEqual(sorted(router.schemas), ["critic", "detective", "hook", "metadata", "script"])
        self.assertIn("detective_state", baglam)
        self.assertIn("hook_state", baglam)

        router.schemas.clear()
        router.prompts.clear()
        agentic_icerik_uretimi(router, {}, {}, {}, 30, "dengeli", "Autonoe", lambda m: None,
                               mod_karari={"mode": "DUO"}, baglam=baglam,
                               qa_geri_bildirimi="- tone_check: FAIL: fazla resmi")
        # Detective/Hook yeniden kullanılır; Critic KALİTE için yine çalışır.
        self.assertEqual(sorted(router.schemas), ["critic", "metadata", "script"])
        script_prompt = next(p for p in router.prompts if "Sohbet Yazarı" in p)
        self.assertIn("fazla resmi", script_prompt)
        critic_prompt = next(p for p in router.prompts if "trol, şüpheci" in p)
        self.assertIn("fazla resmi", critic_prompt)
        self.assertIn("GERİ ALDIRMAMALI", critic_prompt)

    @_kelime
    @_sure
    @_duo
    def test_failed_detective_and_hook_are_retried_on_qa_regen_not_reused(self, *_):
        router = _CountingRouter()
        orijinal = router.metin_uret
        dusur = {"detective", "hook"}

        def metin_uret(content, prompt, schema, log, **kw):
            props = schema.get("properties", {})
            ad = "detective" if "kronik_sikayetler" in props else "hook" if "kapak_basliklari" in props else None
            if ad in dusur:
                raise Exception("503 UNAVAILABLE")
            return orijinal(content, prompt, schema, log, **kw)

        router.metin_uret = metin_uret
        baglam = {}
        logs = []
        agentic_icerik_uretimi(router, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append,
                               mod_karari={"mode": "DUO"}, baglam=baglam)
        self.assertFalse(baglam["detective_basarili"])
        # Kapak kurtarma da düştü → yerel set korunur, hook yedekte kalır.
        self.assertFalse(baglam["hook_ajan_basarili"])
        self.assertTrue(any("Kapak kurtarma" in line for line in logs))

        dusur.clear()
        router.schemas.clear()
        agentic_icerik_uretimi(router, {}, {}, {}, 30, "dengeli", "Autonoe", lambda m: None,
                               mod_karari={"mode": "DUO"}, baglam=baglam, qa_geri_bildirimi="- tone_check: FAIL: x")
        self.assertIn("detective", router.schemas)
        self.assertIn("hook", router.schemas)
        self.assertTrue(baglam["detective_basarili"])
        self.assertTrue(baglam["hook_ajan_basarili"])

    @_kelime
    @_sure
    @_duo
    def test_hook_failure_recovers_cover_titles_in_parallel(self, *_):
        router = _CountingRouter()
        orijinal = router.metin_uret
        hook_cagri = {"n": 0}

        def metin_uret(content, prompt, schema, log, **kw):
            if "kapak_basliklari" in schema.get("properties", {}):
                hook_cagri["n"] += 1
                if hook_cagri["n"] == 1:
                    raise Exception("503 UNAVAILABLE")
            return orijinal(content, prompt, schema, log, **kw)

        router.metin_uret = metin_uret
        baglam = {}
        logs = []
        reels, *_rest = agentic_icerik_uretimi(router, {}, {}, {"core_story": "Lexus kokpiti yeniledi"}, 30, "dengeli", "Autonoe", logs.append,
                                               mod_karari={"mode": "DUO"}, baglam=baglam)
        self.assertEqual(hook_cagri["n"], 2)
        self.assertEqual(reels["kapak_basliklari"][0]["ust"], "BU SUV NORMAL DEĞİL")
        self.assertTrue(baglam["hook_ajan_basarili"])
        self.assertTrue(any("Kapak kurtarma: Hook ajanı ikinci denemede yanıt verdi" in line for line in logs))

    @_kelime
    @_sure
    @_duo
    def test_failed_hook_regeneration_keeps_previous_model_hook(self, *_):
        router = _CountingRouter()
        baglam = {}
        agentic_icerik_uretimi(router, {}, {}, {}, 30, "dengeli", "Autonoe", lambda m: None,
                               mod_karari={"mode": "DUO"}, baglam=baglam)
        orijinal = router.metin_uret

        def metin_uret(content, prompt, schema, log, **kw):
            if "kapak_basliklari" in schema.get("properties", {}):
                raise Exception("503 UNAVAILABLE")
            return orijinal(content, prompt, schema, log, **kw)

        router.metin_uret = metin_uret
        baglam["hook_yenile"] = True
        reels, *_rest = agentic_icerik_uretimi(router, {}, {}, {}, 30, "dengeli", "Autonoe", lambda m: None,
                                               mod_karari={"mode": "DUO"}, baglam=baglam, qa_geri_bildirimi="- hook_check: FAIL: zayıf")
        self.assertEqual(reels["kapak_basliklari"][0]["ust"], "BU SUV NORMAL DEĞİL", "model kapak seti şablonla ezilmez")

    @_kelime
    @_sure
    @_duo
    def test_cover_fail_with_voice_regenerates_hook_with_feedback(self, *_):
        router = _CountingRouter()
        baglam = {}
        agentic_icerik_uretimi(router, {}, {}, {}, 30, "dengeli", "Autonoe", lambda m: None,
                               mod_karari={"mode": "DUO"}, baglam=baglam)
        router.schemas.clear()
        router.prompts.clear()
        baglam["hook_yenile"] = True
        agentic_icerik_uretimi(router, {}, {}, {}, 30, "dengeli", "Autonoe", lambda m: None,
                               mod_karari={"mode": "DUO"}, baglam=baglam, qa_geri_bildirimi="- cover_check: FAIL: aynı")
        self.assertIn("hook", router.schemas)
        self.assertNotIn("detective", router.schemas)
        hook_prompt = next(p for p in router.prompts if "Kanca Üreticisi" in p)
        self.assertIn("cover_check: FAIL: aynı", hook_prompt)

    @_kelime
    @_sure
    @_duo
    def test_mode_decision_may_be_lazy_callable(self, *_):
        router = _CountingRouter()
        order = []

        def lazy_mode():
            order.append(list(router.schemas))
            return {"mode": "SOLO_FEMALE"}

        _, _, plan, script, *_ = agentic_icerik_uretimi(
            router, {}, {}, {}, 30, "dengeli", "Autonoe", lambda m: None, mod_karari=lazy_mode)
        self.assertEqual(plan["mode"], "SOLO_FEMALE")
        # Mod kararı Detective + Hook bittikten sonra, Script'ten önce çözüldü.
        self.assertEqual(order, [["detective", "hook"]])
        self.assertEqual({s["speaker"] for s in script["segments"]}, {"female"})

    @_kelime
    @_sure
    def test_metadata_runs_concurrently_with_tts(self, *_):
        router = _CountingRouter()
        metadata_started = threading.Event()
        original = router.metin_uret

        def metin_uret(content, prompt, schema, log, **kw):
            if "reels_hashtag" in schema.get("properties", {}):
                metadata_started.set()
            return original(content, prompt, schema, log, **kw)

        router.metin_uret = metin_uret

        def slow_tts(router_, segments, output_path, log, hiz_carpani=1.0):
            # Metadata TTS bitmeden başlamış olmalı.
            self.assertTrue(metadata_started.wait(2), "Metadata TTS ile paralel başlamadı")
            Path(output_path).write_bytes(b"fake")
            return True, "tts"

        with patch("core.agentic.duo_ses_uret", side_effect=slow_tts):
            *_, meta = agentic_icerik_uretimi(router, {}, {}, {}, 30, "dengeli", "Autonoe", lambda m: None,
                                              mod_karari={"mode": "DUO"})
        self.assertEqual(meta.get("reels_aciklama"), "açıklama")


class WebSearchParallelTests(unittest.TestCase):
    def test_queries_run_in_parallel_and_keep_order(self):
        state = {"video_identity": {"brand": "Lexus", "exact_model": "ES 300h"},
                 "viral_arastirma_ihtiyaclari": ["a", "b"]}
        active = {"now": 0, "max": 0}
        lock = threading.Lock()

        def fake_sorgu(sorgu, max_sonuc=4, log_ekle=None, hata_bildir=None):
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.05)
            with lock:
                active["now"] -= 1
            return [{"baslik": sorgu, "icerik": "x", "kaynak": ""}]

        with patch.object(web_search, "duckduckgo_sorgu", side_effect=fake_sorgu):
            text = web_search.web_arastirma_yap(state, lambda m: None)
        sorgular = web_search.arastirma_sorgulari_olustur(state)
        positions = [text.index(f"SORGU: {q}") for q in sorgular]
        self.assertEqual(positions, sorted(positions))
        self.assertGreater(active["max"], 1)


    def test_failed_parallel_queries_are_retried_serially(self):
        state = {"video_identity": {"brand": "Lexus", "exact_model": "ES 300h"},
                 "viral_arastirma_ihtiyaclari": ["a", "b"]}
        sorgular = web_search.arastirma_sorgulari_olustur(state)
        cagri = {}
        lock = threading.Lock()

        def fake_sorgu(sorgu, max_sonuc=4, log_ekle=None, hata_bildir=None):
            with lock:
                cagri[sorgu] = cagri.get(sorgu, 0) + 1
                ilk = cagri[sorgu] == 1
            if sorgu == sorgular[0] and ilk:
                hata_bildir()  # hız sınırı
                return []
            if sorgu == sorgular[1]:
                return []  # gerçekten sonuçsuz: tekrar denenmez
            return [{"baslik": sorgu, "icerik": "x", "kaynak": ""}]

        logs = []
        with patch.object(web_search, "duckduckgo_sorgu", side_effect=fake_sorgu), \
             patch.dict(os.environ, {"WEB_SEARCH_RETRY_DELAY": "1"}), patch.object(web_search.time, "sleep"):
            text = web_search.web_arastirma_yap(state, logs.append)
        self.assertEqual(cagri[sorgular[0]], 2)
        self.assertEqual(cagri[sorgular[1]], 1)
        self.assertIn(f"SORGU: {sorgular[0]}", text)
        self.assertTrue(any("seri olarak yeniden deneniyor" in line for line in logs))


class CoverCropTests(unittest.TestCase):
    def test_cropped_alt_has_no_dangling_quote_or_conjunction(self):
        alt = alt_baslik_duzenle("Lüks segmentte 'eski hataların düzeltilmesi' ve 'teknolojik yeniliklerin' birleşimi")
        self.assertNotIn("'", alt)
        self.assertFalse(alt.endswith(" ve"))

    def test_local_fallback_does_not_use_cropped_sentences(self):
        editorial = {
            "core_story": "Lüks otomobil markalarının multimedya ekranlarında fiziksel tuşlardan vazgeçmesi eleştiriliyor",
            "potential_hook_territories": ["Lüks segmentte 'eski hataların düzeltilmesi' ve 'teknolojik yeniliklerin' birleşimi"],
        }
        basliklar = kapak_basliklarini_normalize_et(None, editorial, {}, {})
        self.assertEqual(len(basliklar), 5)
        for item in basliklar:
            self.assertTrue(alt_kurala_uygun_mu(item["alt"]))
            self.assertNotIn("fiziksel tuşlardan", item["alt"])
            self.assertNotIn("'teknolojik", item["alt"])


class LoadingEditorTests(unittest.TestCase):
    def test_progress_edits_are_async_coalesced_and_flushed(self):
        import telegram.telegram_pipeline_worker as worker
        sent = []
        gate = threading.Event()

        def fake_edit(message_id, text):
            gate.wait(2)
            sent.append(text)

        with patch.object(worker, "edit_message", side_effect=fake_edit):
            editor = worker._LoadingEditor(1)
            started = time.perf_counter()
            for i in range(5):
                editor.submit(f"t{i}")
            self.assertLess(time.perf_counter() - started, 0.5, "submit pipeline'ı bekletmemeli")
            gate.set()
            editor.flush(2)
        self.assertEqual(sent[-1], "t4")
        self.assertLessEqual(len(sent), 2)


if __name__ == "__main__":
    unittest.main()
