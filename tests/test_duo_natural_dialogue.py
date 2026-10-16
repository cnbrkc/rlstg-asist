"""DUO doğallık regresyonları: cümle paslaşması değil, tamamlanan düşünceler.

Toplam süre/kelime sözleşmesi korunur; DUO'da tur sayısı ve 1-3 cümle sınırı
kaldırılır. Kısa tepkiler serbesttir ama zorunlu değildir. Yazar, uzatma,
eleştirmen, QA ve TTS aynı anlam-odaklı söz alma yaklaşımını kullanmalıdır.
Bu testler prompt sözleşmesini sınar; canlı modelin doğallığını ölçmez.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.agentic import (
    _critic_calistir,
    _script_writer_calistir,
    _segment_butcesi_olustur,
)
from core.prompts import qa_promptunu_olustur
from core.router import SmartRouter
from core.tts_delivery import replik_tts_hazirla, teslimat_blogu

SURE = 120


class _Router:
    """Tek yanıt veren, promptları kaydeden sahte router."""

    def __init__(self, response):
        self.response = response
        self.prompts = []

    def metin_uret(self, *args, **kwargs):
        self.prompts.append(args[1] if len(args) > 1 else kwargs.get("prompt", ""))
        return self.response, "fake-model"


def _bos_script():
    return {
        "segments": [
            {"speaker": "female", "tts_tag": "[şaşırarak]", "text": "Örnek replik."},
        ],
        "yorum_tetikleyici_soru": "Siz ne diyorsunuz?",
    }


class CumleButcesiRitimTestleri(unittest.TestCase):
    """Toplam bütçe korunmalı ama söz alma cümle sayısıyla sınırlandırılmamalı."""

    def setUp(self):
        self.butce = _segment_butcesi_olustur(350, 315, 385, "DUO")

    def test_cumle_sayaci_korunur(self):
        """Süre sözleşmesi testleri bu sabitleri bekler; bozulmamalı."""
        self.assertIn("CÜMLE TABANLI", self.butce)
        self.assertIn("TAM 32 CÜMLE", self.butce)
        self.assertIn("TEK TEK SAY", self.butce)

    def test_kisa_tepki_cumleleri_serbest_ve_istenen(self):
        self.assertIn("KISA TEPKİ", self.butce)
        self.assertIn("1-5 kelimelik", self.butce)
        self.assertIn("Oha!", self.butce)
        # Kısa tepkiler ritim kırıcıdır; 'boşluk' muamelesi görmemeli.
        self.assertIn("boşluk değildir", self.butce)

    def test_doldurma_yasagi_korunur_ama_tepki_serbest(self):
        self.assertIn("doldurmaları YASAK", self.butce)
        self.assertIn("tepki + karşı argüman", self.butce)
        self.assertIn("SERBEST", self.butce)

    def test_mekanik_salinim_yasak(self):
        self.assertIn("mekanik salınımına DİZME", self.butce)

    def test_uzun_dusunce_bloklari_ve_kotasiz_tur(self):
        self.assertIn("Tur sayısı için alt/üst kota YOK", self.butce)
        self.assertIn("3-5 cümle", self.butce)
        self.assertIn("daha uzun olabilir", self.butce)
        self.assertIn("tepki kotası YOK", self.butce)
        self.assertNotIn("her tur 1-3 cümle", self.butce)
        self.assertNotIn("16-32 tura böl", self.butce)

    def test_kisa_ve_uzun_videoda_tur_kotasi_yok(self):
        for hedef in (35, 90, 175, 350, 700):
            with self.subTest(hedef=hedef):
                butce = _segment_butcesi_olustur(hedef, hedef * .9, hedef * 1.1, "DUO")
                self.assertNotIn("tura böl", butce)
                self.assertNotIn("her tur 1-3", butce)
                self.assertIn("toplam süre/kelime bütçesini", butce)
                self.assertIn("3/5 şablonu DEĞİL", butce)

    def test_kisa_tepki_cumlesi_hedefi_degistirmez(self):
        """350 kelime hedefi için cümle hedefi değişmemeli (test sözleşmesi)."""
        self.assertIn("TAM 32 CÜMLE", _segment_butcesi_olustur(350, 315, 385, "DUO"))
        self.assertIn("TAM 16 CÜMLE", _segment_butcesi_olustur(175, 160, 195, "SOLO_FEMALE"))


class DuoDiyalogPromptTestleri(unittest.TestCase):
    """Script Writer, kısa paslaşma yerine birbirine bağlı düşünceler istemeli."""

    def setUp(self):
        router = _Router(_bos_script())
        _script_writer_calistir(
            router, {"kapak_metni": "Fiyat"}, {"viral_kan_mali": "bilgi"},
            {}, {}, {}, SURE, lambda *_: None,
            mod="DUO", hedef_kelime=350, hedef_kelime_bilgisi="Hedef 350 kelime.",
            segment_butcesi=_segment_butcesi_olustur(350, 315, 385, "DUO"),
        )
        self.prompt = router.prompts[0]

    def test_atisma_ve_kiskirtma_isteniyor(self):
        self.assertIn("ATIŞMA VE KIŞKIRTMA", self.prompt)
        self.assertIn("Karşılıklı atışma", self.prompt)
        self.assertIn("seyirciyi kışkırtan", self.prompt)
        self.assertIn("kendi istediklerini söylüyor", self.prompt)

    def test_tepkiler_serbest_ama_zorunlu_degil(self):
        self.assertIn("KONUŞMA TEPKİLERİ", self.prompt)
        self.assertIn("SERBEST BIRAK ama kota koyma", self.prompt)
        self.assertIn("aynı segmentte sürdür", self.prompt)
        self.assertNotIn("can damarıdır", self.prompt)

    def test_dusunce_bloklari_ve_asimetri(self):
        self.assertIn("DÜŞÜNCE BLOKLARI", self.prompt)
        self.assertIn("Tur/cümle kotası YOK", self.prompt)
        self.assertIn("ASİMETRİK RİTİM", self.prompt)
        self.assertIn("Mekanik kadın-erkek-kadın salınımı", self.prompt)
        self.assertIn("yeni bir 3/5 rutini kurma", self.prompt)
        self.assertNotIn("ilk iki tur mümkünse farklı", self.prompt)
        self.assertNotIn("her tur 1-3 cümle", self.prompt)

    def test_cevap_anlamsal_olarak_onceki_soze_bagli(self):
        self.assertIn("iddiasını, kaygısını veya sonucunu", self.prompt)
        self.assertIn("cevap hiç değişmeden çalışıyorsa yeniden yaz", self.prompt)
        self.assertIn("iki monoloğa dönüşmesin", self.prompt)
        self.assertIn("iki kişi de bilgi verebilir", self.prompt)

    def test_duygu_degisimi_kotasi_yok(self):
        self.assertIn("aynı etiket sürebilir", self.prompt)
        self.assertNotIn("arka arkaya aynı etiketi kullanma", self.prompt)
        self.assertNotIn("etiketleri dönüşümlü kullan", self.prompt)

    def test_uzatma_ve_qa_revizyonu_ayni_diyalog_kurallarini_tasir(self):
        for ek in (
            {"mevcut_script": _bos_script(), "mevcut_kelime": 100, "eksik_kelime": 250},
            {"qa_geri_bildirimi": "Mekanik kısa paslaşmayı düzelt."},
            {"feedback": "Cevap önceki sözü ele alsın."},
        ):
            with self.subTest(ek=ek):
                router = _Router(_bos_script())
                _script_writer_calistir(
                    router, {}, {}, {}, {}, {}, SURE, lambda *_: None,
                    mod="DUO", hedef_kelime=350, **ek,
                )
                prompt = router.prompts[0]
                self.assertIn("DÜŞÜNCE BLOKLARI", prompt)
                self.assertIn("Tur/cümle kotası YOK", prompt)
                self.assertIn("kısa videoda uzun tur zorlaması yapma", prompt)
                self.assertNotIn("arka arkaya aynı etiketi kullanma", prompt)

    def test_tts_etiketi_zorunlu(self):
        self.assertIn("boş bırakma", self.prompt)
        self.assertIn("tts_tag", self.prompt)

    def test_omurga_ve_callback_korunmus(self):
        """Var olan yapısal kurallar silinmemeli."""
        self.assertIn("HOOK → FRICTION → PROOF → REVERSAL → PAYOFF/CALLBACK", self.prompt)
        self.assertIn("LEXİCAL UPTAKE", self.prompt)
        self.assertIn("callback", self.prompt.casefold())

    def test_solo_mod_diyalog_yasagi_korunmus(self):
        router = _Router(_bos_script())
        _script_writer_calistir(
            router, {"kapak_metni": "Fiyat"}, {"viral_kan_mali": "bilgi"},
            {}, {}, {}, SURE, lambda *_: None,
            mod="SOLO_FEMALE", hedef_kelime=350, hedef_kelime_bilgisi="Hedef 350 kelime.",
            segment_butcesi=_segment_butcesi_olustur(350, 315, 385, "SOLO_FEMALE"),
        )
        solo = router.prompts[0]
        self.assertIn("Diyalog kalıbı, ikinci karaktere hitap veya soru-cevap boşluğu YASAK", solo)
        self.assertNotIn("ATIŞMA VE KIŞKIRTMA", solo)


class TepkiStilleriTestleri(unittest.TestCase):
    """Yeni tepki stilleri İngilizce teslimat notuna çevrilmeli (okunmaz)."""

    def test_yeni_tepki_etiketleri_cevrilir(self):
        ornekler = [
            ("[tepki]", "reaction"),
            ("[onaylayarak]", "approval"),
            ("[umursamaz]", "dismissive"),
            ("[abartarak]", "exaggeration"),
            ("[israrla]", "insistent"),
            ("[eğlenerek]", "amused"),
        ]
        for etiket, beklenen in ornekler:
            konusma, stil = replik_tts_hazirla("Bu fiyat normal mi?", etiket)
            self.assertEqual("Bu fiyat normal mi?", konusma)
            self.assertIn(beklenen, stil, f"{etiket} → {stil!r} içinde {beklenen} yok")
            self.assertNotIn(etiket.strip("[]"), konusma.casefold())

    def test_teslimat_blogu_tepki_notlarini_yazar(self):
        blok = teslimat_blogu([
            {"speaker": "female", "text": "Oha, bu ciddi mi?", "tts_tag": "[tepki]"},
            {"speaker": "male", "text": "Bence abartılıyor.", "tts_tag": "[umursamaz]"},
            {"speaker": "female", "text": "Yok artık!", "tts_tag": "[şaşırarak]"},
        ])
        self.assertIn("Turn 1 (female)", blok)
        self.assertIn("reaction", blok)
        self.assertIn("dismissive", blok)
        self.assertIn("surprise", blok)
        # Türkçe etiketler notlara sızmamalı.
        for yasak in ("tepki]", "umursamaz", "şaşırarak"):
            self.assertNotIn(yasak, blok)

    def test_varsayilan_stiller_korunmus(self):
        konusma, stil = replik_tts_hazirla("[alaycı] Alman tekeli bitiyor", "[vurgulu]")
        self.assertEqual("Alman tekeli bitiyor", konusma)
        self.assertIn("sarcasm", stil)
        self.assertIn("stress", stil)


class TtsYonetmenNotlariTestleri(unittest.TestCase):
    """Multi-speaker TTS sesli tepkilere izin vermeli; düet yasağı korunmalı."""

    def setUp(self):
        router = SmartRouter.__new__(SmartRouter)
        self.prompt = router._tts_coklu_promptu_olustur(
            "Autonoe: Oha!\nCharon: Rakam bu.",
            ["Autonoe", "Charon"],
            "Turn 1 (Autonoe): instant genuine reaction",
        )

    def test_duet_yasagi_korunmus(self):
        self.assertIn("Do not sing, chant, harmonize", self.prompt)
        self.assertIn("#### TRANSCRIPT", self.prompt)

    def test_sesli_tepkilere_izin_var(self):
        self.assertIn("AUDIBLE", self.prompt)
        self.assertIn("chuckle", self.prompt)
        self.assertIn("scoff", self.prompt)

    def test_uzun_tur_tek_dusunce_olarak_seslendirilir(self):
        self.assertIn("two or three lines in a row", self.prompt)
        self.assertIn("three to five sentences or more", self.prompt)
        self.assertIn("not a fresh announcement at every sentence", self.prompt)
        self.assertIn("do not insert reactions or extra speaker changes", self.prompt)

    def test_atma_enerjisi_tanimli(self):
        casefold = self.prompt.casefold()
        self.assertIn("reaction", casefold)

    def test_solo_teslimat_promptu_da_tesiz_degil(self):
        router = SmartRouter.__new__(SmartRouter)
        solo = router._tts_performans_promptu_olustur("Merhaba dünya.", "Autonoe", "")
        self.assertIn("one emotional register", solo)
        self.assertIn("chuckle", solo)


class QaMonotonlukKontrolTestleri(unittest.TestCase):
    """Final QA duo_check monoton ritmi ve tepkisiz okumayı FAIL saymalı."""

    def setUp(self):
        self.qa = qa_promptunu_olustur("dengeli")

    def test_tepki_replikleri_sorgulanıyor(self):
        self.assertIn("Kısa tepki replikleri", self.qa)
        self.assertIn("karşılıklı atışma", self.qa)

    def test_ust_uste_konusma_ve_mekanik_okuma_fail(self):
        self.assertIn("2-3 tur üst üste", self.qa)
        self.assertIn("monoton ritim", self.qa)
        self.assertIn("kadın-erkek-kadın-erkek", self.qa)
        self.assertNotIn("kışkırtmayan düz anlatım da FAIL", self.qa)
        self.assertIn("bunlar yok diye FAIL verme", self.qa)
        self.assertIn("uzun diye FAIL verme", self.qa)
        self.assertIn("kısa videoda uzun tur zorunlu değildir", self.qa)
        self.assertIn("tek başına FAIL değildir", self.qa)

    def test_segment_sayisi_degil_gercek_soz_alma_ve_anlam(self):
        self.assertIn("SÖZ ALMA BAZINDA", self.qa)
        self.assertIn("ardışık segmentlerini tek blok say", self.qa)
        self.assertIn("cevabı değişmeden çalışıyorsa", self.qa)

    def test_mevcut_duo_kontrolleri_korunmus(self):
        self.assertIn("hook→friction→proof→reversal→payoff", self.qa)
        self.assertIn("şarkıcı düeti", self.qa)


class CriticDusunceBlokTestleri(unittest.TestCase):
    def test_elestirmen_yazari_kisa_paslasmaya_geri_itmez(self):
        router = _Router({"score": 8, "approved": True, "feedback": ""})
        _critic_calistir(router, _bos_script(), {}, lambda *_: None)
        prompt = router.prompts[0]
        self.assertIn("3-5 cümlelik veya daha uzun", prompt)
        self.assertIn("kısa videoda uzun tur zorunlu değildir", prompt)
        self.assertIn("eksiklikleri tek başına red gerekçesi değildir", prompt)
        self.assertIn("önceki kişinin kaygısına/iddiasına", prompt)
        self.assertIn("ardışık segmentlerini tek söz alma", prompt)


if __name__ == "__main__":
    unittest.main()
