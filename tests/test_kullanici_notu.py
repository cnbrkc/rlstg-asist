"""Kullanıcı notunun (Telegram'da videoyla gönderilen açıklama) üretim
katmanlarına gerçekten taşındığını kilitler.

Kök neden (18.10.2026 kullanıcı şikâyeti): "Video açıklamasına yazdığım şeyi
kaale almıyor; gönderdiğim aracı başka bir şeye benzetip sürekli onun
videosunu hazırlıyor."

Teknik kök neden: `pipeline._qa_regeneration_loop` `production_notes`
parametresini alıyordu ama FONKSİYON GÖVDESİNDE HİÇ KULLANMIYORDU. Not yalnızca
Forensic ve Editorial'a gidiyor; Research, Detective, Hook, Script Writer,
Metadata, Caption, Threads ve Final QA notu hiç görmüyordu. Forensic aşaması
aracı yanlış modele benzettiğinde üretimin geri kalanı o yanlış kimliği takip
ediyordu.

Bu testler notun HER katmana ulaştığını ve kimlik çelişkisinde ek araştırma
tetiklendiğini gerçek kod yolları üzerinden doğrular.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core import pipeline as pl
from core.pipeline import (
    _caption_calistir, _forensic_analiz_calistir, _qa_calistir, _threads_calistir,
)
from core.prompts import (
    KULLANICI_NOTU_BASLIGI, kullanici_notu_icerik, kullanici_notu_kurallari,
    kullanici_notu_temizle,
)
from core.web_search import kimlik_notla_celisiyor_mu

NOT = "Bu araç Mission S; videodaki model kesinlikle Mission S. Fiyat konuşma, sadece sürüş hissine odaklan."
KILIT = "KULLANICI NOTU KİLİDİ"


def _mock_duo_ses(router, segments, output_path, log, hiz_carpani=1.0):
    Path(output_path).write_bytes(b"fake-duo-audio")
    return True, "fake-duo-tts"


class _KayitRouter:
    """Her ajan çağrısının (content, prompt) ikilisini şema adıyla kaydeder."""

    def __init__(self):
        self.kayitlar = []

    def _ad(self, schema):
        props = (schema or {}).get("properties", {})
        if "kronik_sikayetler" in props:
            return "detective"
        if "kapak_basliklari" in props:
            return "hook"
        if "segments" in props:
            return "script"
        if "score" in props:
            return "critic"
        if "reels_aciklama" in props:
            return "metadata"
        if "overall" in props:
            return "qa"
        if "threads_aciklamasi" in props:
            return "threads"
        if "reels_aciklamasi" in props:
            return "caption"
        if "turkiye_satis_durumu" in props or "facts" in props:
            return "research"
        if "video_identity" in props:
            return "forensic"
        if "anlatim_modu" in props:
            return "anlatim_modu"
        return "editorial"

    def metin_uret(self, content, prompt, schema, log, arama_kullan=False, **_):
        ad = self._ad(schema)
        self.kayitlar.append({"ad": ad, "content": str(content), "prompt": str(prompt)})
        if ad == "detective":
            return {"kronik_sikayetler": ["x"], "turkiye_ozel_magduriyet": "", "viral_kan_mali": ""}, "m"
        if ad == "hook":
            return {
                "secilen_sablon": "Ters_Kose",
                "kapak_basliklari": [
                    {"ust": "BU ARAÇ NORMAL DEĞİL", "alt": "Görünenden farklı bir hikâye var"},
                    {"ust": "GERÇEK RAKAM ŞOK", "alt": "Kâğıt üzerindeki rakam gerçeği anlatmıyor"},
                    {"ust": "KİMSE BEKLEMİYORDU", "alt": "Sonuna kadar izleyince mesele netleşiyor"},
                    {"ust": "ALMADAN ÖNCE İZLE", "alt": "Bu kararı vermeden önce bunu bil"},
                    {"ust": "DETAY FARKI BÜYÜK", "alt": "Kullanıcıların şikâyet ettiği nokta bu"},
                ],
                "kapak_metni": "BU ARAÇ NORMAL DEĞİL",
                "ilk_3_saniye_kanca": "Bu fiyat herkesi şaşırttı",
            }, "m"
        if ad == "script":
            return {"segments": [{"speaker": "female", "tts_tag": "", "text": "Bu araç gerçekten Mission S mi?"},
                                 {"speaker": "male", "tts_tag": "", "text": "Sürüş hissi bunu doğruluyor."}],
                    "yorum_tetikleyici_soru": "Siz alır mıydınız?"}, "m"
        if ad == "critic":
            return {"score": 9, "approved": True, "feedback": ""}, "m"
        if ad == "metadata":
            return {"reels_baslik": "T", "reels_aciklama": "açıklama", "reels_hashtag": ["#oto"]}, "m"
        if ad == "research":
            return {"facts": [], "turkiye_satis_durumu": "BILINMIYOR"}, "m"
        if ad == "qa":
            return {"overall": "PASS", "regeneration_targets": []}, "m"
        if ad == "threads":
            return {"threads_aciklamasi": "Threads metni."}, "m"
        if ad == "caption":
            return {"reels_aciklamasi": "caption", "reels_hashtagleri": ["#oto"]}, "m"
        return {}, "m"

    def video_analiz_et(self, video_bytes, mime_type, prompt, schema, log, **_):
        self.kayitlar.append({"ad": "forensic", "content": "", "prompt": str(prompt)})
        return {"video_identity": {"brand": "Mission", "exact_model": "S"}, "observed_facts": []}, "m"

    def ses_uret(self, text, voice, output, log, hiz_carpani=1.0, **kwargs):
        Path(output).write_bytes(b"fake-solo-audio")
        return True, "fake-tts"

    def _girdi(self, ad):
        return [k for k in self.kayitlar if k["ad"] == ad]


class NotTemizlemeTests(unittest.TestCase):
    def test_eski_worker_oneki_temizlenir(self):
        ham = f"KULLANICI TELEGRAM NOTU (MUTLAK ÖNCELİKLİ):\n{NOT}"
        self.assertEqual(kullanici_notu_temizle(ham), " ".join(NOT.split()))

    def test_bos_ve_none_girdi_bos_doner(self):
        for deger in (None, "", "   ", 0):
            self.assertEqual(kullanici_notu_temizle(deger), "")
            self.assertEqual(kullanici_notu_icerik(deger), "")
            self.assertEqual(kullanici_notu_kurallari(deger), "")

    def test_kurallar_not_metnini_tekrarlamaz(self):
        """Not prompt'ta BİR KEZ görünmeli: kurallar bloğu metni kopyalarsa
        hem token şişer hem test_prompts'taki tek-seferlik sözleşmesi bozulur."""
        kurallar = kullanici_notu_kurallari(NOT)
        self.assertIn(KILIT, kurallar)
        self.assertNotIn("Mission S", kurallar)
        self.assertIn("Mission S", kullanici_notu_icerik(NOT))
        self.assertIn(KULLANICI_NOTU_BASLIGI, kullanici_notu_icerik(NOT))


class ForensicNotTests(unittest.TestCase):
    def test_forensic_promptu_notu_kurallarla_bir_kez_tasir(self):
        router = _KayitRouter()
        _forensic_analiz_calistir(router, b"", "video/mp4", NOT, 30, lambda m: None)
        prompt = router._girdi("forensic")[0]["prompt"]
        # Not metni prompt'a BİR KEZ girmeli (çift enjeksiyon prompt'u şişirir).
        self.assertEqual(prompt.count(NOT), 1)
        self.assertIn(KILIT, prompt)


class ResearchNotTests(unittest.TestCase):
    def _calistir(self, video_state, not_metni, ek_arama="EK ARAŞTIRMA BLOĞU"):
        router = _KayitRouter()
        logs = []
        with patch.object(pl, "web_arastirma_yap", return_value="WEB SONUÇLARI"), \
             patch.object(pl, "kullanici_notu_arastirmasi", return_value=ek_arama) as ek_mock, \
             patch.object(pl, "otv_ek_arastirma", return_value=""), \
             patch.object(pl, "otv_kilidi_hesapla", return_value={"durum": "YOK"}), \
             patch.object(pl, "vergi_kilidini_uygula", side_effect=lambda s, k, *a, **b: s):
            pl._research_calistir(router, video_state, logs.append, kullanici_notu=not_metni)
        return router, logs, ek_mock

    def test_not_research_girdisine_ve_promptuna_girer(self):
        router, _logs, ek_mock = self._calistir(
            {"video_identity": {"brand": "Mission", "exact_model": "S"}}, NOT,
        )
        kayit = router._girdi("research")[0]
        self.assertIn("Mission S", kayit["content"])
        self.assertIn(KULLANICI_NOTU_BASLIGI, kayit["content"])
        self.assertIn(KILIT, kayit["prompt"])
        # Kimlik notla uyumlu: ek araştırma turu gereksiz yere çalışmaz.
        ek_mock.assert_not_called()

    def test_kimlik_celiskisinde_ek_arama_calisir(self):
        router, logs, ek_mock = self._calistir(
            {"video_identity": {"brand": "BYD", "exact_model": "Tang"}}, NOT,
        )
        ek_mock.assert_called_once()
        kayit = router._girdi("research")[0]
        self.assertIn("EK ARAŞTIRMA BLOĞU", kayit["content"])
        self.assertTrue(any("uyuşmuyor" in line for line in logs))

    def test_not_yoksa_research_ayni_kalir(self):
        router, logs, ek_mock = self._calistir({"video_identity": {"brand": "BYD", "exact_model": "Tang"}}, "")
        kayit = router._girdi("research")[0]
        self.assertNotIn(KULLANICI_NOTU_BASLIGI, kayit["content"])
        self.assertNotIn(KILIT, kayit["prompt"])
        ek_mock.assert_not_called()
        self.assertFalse([line for line in logs if "uyuşmuyor" in line])


class KimlikCeliskisiTests(unittest.TestCase):
    def test_not_bos_veya_kimlik_yoksa_celiski_yoktur(self):
        self.assertFalse(kimlik_notla_celisiyor_mu({"video_identity": {"brand": "Kia", "exact_model": "EV3"}}, ""))
        self.assertFalse(kimlik_notla_celisiyor_mu({"video_identity": {"brand": "UNKNOWN", "exact_model": "UNKNOWN"}}, NOT))
        self.assertFalse(kimlik_notla_celisiyor_mu({}, NOT))

    def test_marka_veya_model_notta_geciyorsa_celiski_yoktur(self):
        self.assertFalse(kimlik_notla_celisiyor_mu({"video_identity": {"brand": "Mission", "exact_model": "S"}}, NOT))
        self.assertFalse(kimlik_notla_celisiyor_mu({"video_identity": {"brand": "Kia", "exact_model": "EV3"}}, "kia ev3 videosu"))

    def test_hicbir_kimlik_parcasi_notta_yoksa_celiski_var(self):
        self.assertTrue(kimlik_notla_celisiyor_mu({"video_identity": {"brand": "BYD", "exact_model": "Tang"}}, NOT))

    def test_turkce_karakter_farkliligi_celiski_sayilmaz(self):
        """'Seltos'/'SELTOS' ve diakritik farkı yanlış alarm üretmemeli."""
        self.assertFalse(kimlik_notla_celisiyor_mu(
            {"video_identity": {"brand": "Kia", "exact_model": "Seltos"}},
            "Bu araç KIA SELTOS, başka model yazma.",
        ))


class AgenticNotTests(unittest.TestCase):
    """Notun 4 ajana ve Metadata'ya gerçekten gittiğini doğrular."""

    _kelime = patch("core.agentic._reels_kelime_ayarlarini_hazirla", return_value=(10, 1, 999, 2.5, 5))
    _sure = patch("core.agentic._ses_suresini_al", return_value=25.0)
    _duo = patch("core.agentic.duo_ses_uret", side_effect=_mock_duo_ses)

    @_kelime
    @_sure
    @_duo
    def test_not_tum_uretim_ajanlarina_gider(self, *_):
        from core.agentic import agentic_icerik_uretimi

        router = _KayitRouter()
        agentic_icerik_uretimi(router, {}, {}, {}, 30, "dengeli", "Autonoe", lambda m: None,
                               mod_karari={"mode": "DUO"}, production_notes=NOT)
        gorulen = {k["ad"] for k in router.kayitlar}
        for ad in ("detective", "hook", "script", "metadata"):
            with self.subTest(ajan=ad):
                self.assertIn(ad, gorulen)
                kayitlar = router._girdi(ad)
                self.assertTrue(any("Mission S" in k["content"] for k in kayitlar), f"{ad} content'ında not yok")
                self.assertTrue(any(KILIT in k["prompt"] for k in kayitlar), f"{ad} prompt'unda not kuralı yok")

    @_kelime
    @_sure
    @_duo
    def test_not_yoksa_ajan_girdileri_sismez(self, *_):
        from core.agentic import agentic_icerik_uretimi

        router = _KayitRouter()
        agentic_icerik_uretimi(router, {}, {}, {}, 30, "dengeli", "Autonoe", lambda m: None,
                               mod_karari={"mode": "DUO"})
        for kayit in router.kayitlar:
            self.assertNotIn(KULLANICI_NOTU_BASLIGI, kayit["content"])
            self.assertNotIn(KILIT, kayit["prompt"])


class SosyalKatmanNotTests(unittest.TestCase):
    def test_threads_notu_alir(self):
        router = _KayitRouter()
        _threads_calistir(router, {}, {}, {}, lambda m: None, "dengeli", kullanici_notu=NOT)
        kayit = router._girdi("threads")[0]
        self.assertIn("Mission S", kayit["content"])
        self.assertIn(KILIT, kayit["prompt"])

    def test_caption_notu_alir(self):
        router = _KayitRouter()
        _caption_calistir(router, {"seslendirme_metni": "x"}, {}, {}, {}, lambda m: None, "dengeli", kullanici_notu=NOT)
        kayit = router._girdi("caption")[0]
        self.assertIn("Mission S", kayit["content"])
        self.assertIn(KILIT, kayit["prompt"])

    def test_qa_notu_denetler(self):
        router = _KayitRouter()
        _qa_calistir(router, {}, {}, {}, {"seslendirme_metni": "x"}, {}, {}, 30, lambda m: None,
                     kullanici_notu=NOT)
        kayit = router._girdi("qa")[0]
        self.assertIn("Mission S", kayit["content"])
        self.assertIn(KILIT, kayit["prompt"])


class RegenerationLoopNotTests(unittest.TestCase):
    """Kök nedenin regresyon testi: `_qa_regeneration_loop` production_notes'u
    artık gerçekten ajanlara taşımak zorunda."""

    def test_production_notes_ajanlara_tasiniyor(self):
        wav = Path(os.path.join(os.environ.get("TMPDIR", "/tmp"), "not_test.wav"))
        wav.write_bytes(b"RIFF" + b"0" * 60)
        router = _KayitRouter()
        patches = [
            patch("core.agentic._reels_kelime_ayarlarini_hazirla", return_value=(10, 1, 999, 2.5, 5)),
            patch("core.agentic._ses_suresini_al", return_value=25.0),
            patch("core.agentic.duo_ses_uret", side_effect=_mock_duo_ses),
            patch.object(pl, "_qa_calistir", side_effect=lambda *a, **k: ({"overall": "PASS", "regeneration_targets": []}, "m-qa")),
        ]
        for p in patches:
            p.start()
        try:
            sonuc = pl._qa_regeneration_loop(
                router, {}, {}, {}, {}, {}, {}, {}, {}, 30, "dengeli", "Autonoe", lambda m: None,
                production_notes=NOT,
            )
        finally:
            for p in patches:
                p.stop()
            wav.unlink(missing_ok=True)

        self.assertEqual(len(sonuc), 15)
        adlar = {k["ad"] for k in router.kayitlar}
        self.assertTrue({"detective", "hook", "script", "metadata", "threads"} <= adlar, sorted(adlar))
        for ad in ("detective", "hook", "script", "metadata", "threads"):
            with self.subTest(katman=ad):
                self.assertTrue(any("Mission S" in k["content"] for k in router._girdi(ad)),
                                f"{ad} katmanına kullanıcı notu taşınmadı")

    def test_not_uzunlugu_loglaniyor(self):
        wav = Path(os.path.join(os.environ.get("TMPDIR", "/tmp"), "not_test_log.wav"))
        wav.write_bytes(b"RIFF" + b"0" * 60)
        logs = []
        patches = [
            patch("core.agentic._reels_kelime_ayarlarini_hazirla", return_value=(10, 1, 999, 2.5, 5)),
            patch("core.agentic._ses_suresini_al", return_value=25.0),
            patch("core.agentic.duo_ses_uret", side_effect=_mock_duo_ses),
            patch.object(pl, "_qa_calistir", side_effect=lambda *a, **k: ({"overall": "PASS", "regeneration_targets": []}, "m-qa")),
        ]
        for p in patches:
            p.start()
        try:
            pl._qa_regeneration_loop(
                _KayitRouter(), {}, {}, {}, {}, {}, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append,
                production_notes=NOT,
            )
        finally:
            for p in patches:
                p.stop()
            wav.unlink(missing_ok=True)

        self.assertTrue(any("Kullanıcı notu üretim katmanlarına taşındı" in line for line in logs))


if __name__ == "__main__":
    unittest.main()


class UstSeviyeBaglantiTests(unittest.TestCase):
    """`pipeline_calistir` / `metin_pipeline_calistir` notu aşama fonksiyonlarına
    gerçekten devrediyor mu? (Hata tam bu katmandaydı: değer alınıp düşürülüyordu.)"""

    _QA_DONUS = ({}, "m", {}, {}, False, None, "DUO", "", {}, {}, {"overall": "PASS"}, 0, "m", "m", False)

    def _video_akisi(self, not_metni):
        yakalanan = {}

        def _sahte_mod(*_a, **_k):
            return type("Sonuc", (), {"result": staticmethod(lambda *a, **k: {})})()

        with patch.object(pl, "_resolve_video_duration_strict", return_value=30.0), \
             patch.object(pl, "_forensic_analiz_calistir",
                          side_effect=lambda *a, **k: yakalanan.update(forensic=a) or ({}, "m")), \
             patch.object(pl, "_research_calistir",
                          side_effect=lambda *a, **k: yakalanan.update(research=k) or ({}, "m")), \
             patch.object(pl, "_editorial_calistir",
                          side_effect=lambda *a, **k: yakalanan.update(editorial=a) or ({}, "m")), \
             patch.object(pl, "_anlatim_modu_arka_planda", side_effect=_sahte_mod), \
             patch.object(pl, "_qa_regeneration_loop",
                          side_effect=lambda *a, **k: yakalanan.update(loop=k) or self._QA_DONUS):
            pl.pipeline_calistir(
                router=object(), video_bytes=b"", mime_type="video/mp4", temp_input_video="yok.mp4",
                video_analiz_notlari=not_metni, metin_uretim_notlari=not_metni, sure_saniye=30,
                icerik_tonu="dengeli", secilen_ses_ingilizce="Autonoe", log_ekle=lambda m: None,
            )
        return yakalanan

    def test_video_modu_notu_tum_asamalara_devreder(self):
        yakalanan = self._video_akisi("KULLANICI TELEGRAM NOTU (MUTLAK ÖNCELİKLİ):\nBu araç Mission S.")
        beklenen = "Bu araç Mission S."
        self.assertEqual(yakalanan["forensic"][3], beklenen)
        self.assertEqual(yakalanan["research"]["kullanici_notu"], beklenen)
        self.assertEqual(yakalanan["editorial"][3], beklenen)
        self.assertEqual(yakalanan["loop"]["production_notes"], beklenen)

    def test_not_yokken_bos_deger_gider(self):
        yakalanan = self._video_akisi("   ")
        self.assertEqual(yakalanan["forensic"][3], "")
        self.assertEqual(yakalanan["research"]["kullanici_notu"], "")
        self.assertEqual(yakalanan["loop"]["production_notes"], "")

    def test_metin_modu_kullanici_metnini_not_olarak_tasir(self):
        yakalanan = {}

        def _sahte_mod(*_a, **_k):
            return type("Sonuc", (), {"result": staticmethod(lambda *a, **k: {})})()

        with patch.object(pl, "_research_calistir",
                          side_effect=lambda *a, **k: yakalanan.update(research=k) or ({}, "m")), \
             patch.object(pl, "_editorial_calistir", return_value=({}, "m")), \
             patch.object(pl, "_anlatim_modu_arka_planda", side_effect=_sahte_mod), \
             patch.object(pl, "_qa_regeneration_loop",
                          side_effect=lambda *a, **k: yakalanan.update(loop=k) or self._QA_DONUS):
            pl.metin_pipeline_calistir(
                router=object(), metin="Kia EV3 hakkında konuşalım.", icerik_tonu="dengeli",
                secilen_ses_ingilizce="Autonoe", log_ekle=lambda m: None,
            )
        self.assertEqual(yakalanan["research"]["kullanici_notu"], "Kia EV3 hakkında konuşalım.")
        self.assertEqual(yakalanan["loop"]["production_notes"], "Kia EV3 hakkında konuşalım.")
