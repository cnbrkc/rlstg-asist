"""DUO diyalog doğallığı regresyon testleri (Eylül 2026 monotonluk düzeltmesi).

Kullanıcı şikâyeti: seslendirme "1 cümle erkek 1 cümle kadın" mekanik salınımına
dönmüş; karakterler metni sırayla okuyormuş gibi, konuşma tepkileri kaybolmuş,
teslimat çok monoton.

Kök nedenler ve kilitleyen kontroller:
1. Cümle bütçesi her cümleyi 9-13 kelimeye zorlayıp 'Evet', 'Doğru' gibi kısa
   tepki cümlelerini yasaklayarak ritmi tek düzeliğe itiyordu → artık kısa tepki
   cümleleri serbest/istenen, mekanik salınım açıkça yasak.
2. DUO diyalog kurallarında istenen pozitif desen (atışma, seyirciyi kışkırtma,
   bazen aynı konuşmacının 2-3 tur üst üste konuşması, tepki replikleri) yoktu.
3. tts_tag boş bırakılabiliyor → etiketsiz segment teslimat notuna girmiyor,
   TTS'e sıfır prosodi gidiyordu → her repliğe etiket zorunlu.
4. Multi-speaker TTS yönetmen notları sesli tepkileri tamamen bastırıyordu.
5. QA duo_check monoton ritmi FAIL saymıyordu.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.agentic import _script_writer_calistir, _segment_butcesi_olustur
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
    """Bütçe kısa tepki cümlelerine izin vermeli, mekanik salınımı yasaklamalı."""

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

    def test_ust_uste_konusma_ozgurlugu(self):
        self.assertIn("2-3 tur üst üste", self.butce)
        self.assertIn("1-3 cümle", self.butce)

    def test_kisa_tepki_cumlesi_hedefi_degistirmez(self):
        """350 kelime hedefi için cümle hedefi değişmemeli (test sözleşmesi)."""
        self.assertIn("TAM 32 CÜMLE", _segment_butcesi_olustur(350, 315, 385, "DUO"))
        self.assertIn("TAM 16 CÜMLE", _segment_butcesi_olustur(175, 160, 195, "SOLO_FEMALE"))


class DuoDiyalogPromptTestleri(unittest.TestCase):
    """Script Writer DUO promptu atışma/kışkırtma/tepki desenini istemeli."""

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
        self.assertIn("karşılıklı atışma", self.prompt)
        self.assertIn("seyirciyi kışkırtan", self.prompt)
        self.assertIn("kendi istediklerini söylüyor", self.prompt)

    def test_konusma_tepkileri_can_damari(self):
        self.assertIn("KONUŞMA TEPKİLERİ", self.prompt)
        self.assertIn("can damarı", self.prompt)
        self.assertIn("SERBEST BIRAK", self.prompt)

    def test_ust_uste_konusma_ve_asimetri(self):
        self.assertIn("2-3 replik üst üste", self.prompt)
        self.assertIn("ASİMETRİK RİTİM", self.prompt)
        self.assertIn("Mekanik kadın-erkek-kadın salınımı", self.prompt)

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

    def test_ust_uste_konusma_zemini_var(self):
        self.assertIn("two or three lines in a row", self.prompt)

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
        self.assertIn("kısa tepki replikleri", self.qa)
        self.assertIn("karşılıklı atışma", self.qa)

    def test_ust_uste_konusma_ve_mekanik_okuma_fail(self):
        self.assertIn("2-3 tur üst üste", self.qa)
        self.assertIn("monoton ritim", self.qa)
        self.assertIn("kadın-erkek-kadın-erkek", self.qa)
        self.assertIn("kışkırtmayan düz anlatım da FAIL", self.qa)

    def test_mevcut_duo_kontrolleri_korunmus(self):
        self.assertIn("hook→friction→proof→reversal→payoff", self.qa)
        self.assertIn("şarkıcı düeti", self.qa)


if __name__ == "__main__":
    unittest.main()
