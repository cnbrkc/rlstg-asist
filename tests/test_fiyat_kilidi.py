"""Fiyat kilidi: doğrulanmış Türkiye fiyatı içeriğe zorunlu taşır.

Kök neden (01.10.2026 üretim logu): kullanıcı notu "Kia Seltos Türkiye'de
satışa çıktı. Açıklamada fiyatları verelim, duyuru videosu olacak" idi. Web
aramaları 'UNKNOWN' yüzünden sonuçsuz kalınca Fact Lock'ta fiyat alanı boş
kaldı; ÖTV kilidi matrahı bulamayınca ARALIK'a düştü ve seslendirme ile
açıklama kelime bütçesini "matrah dilimine göre %75 veya %80 veya %90 veya
%100" bracket'larına harcadı — fiyat hiç söylenmedi.

Bu testler: (1) fiyat varsa talimat üretilir ve TL dışı/olumsuz metinler
fiyat sayılmaz, (2) ÖTV ARALIK kanonik ifadesi iki uçla sınırlıdır ve fiyat
cümlesini ezmez, (3) uçtan uca akışta fiyat hem Fact Lock'a hem seslendirme
kilidine ulaşır.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

import core.web_search as web_search
from core.fiyat_kilidi import fiyat_talimati, metindeki_tl_tutarlar, turkiye_fiyati_metni
from core.otv_kilidi import metni_otv_kilidine_cek, otv_kilidi_hesapla, senaryoyu_otv_kilidine_cek
from core.pipeline import _research_calistir


def _video_state():
    return {
        "video_identity": {"brand": "Kia", "exact_model": "Seltos", "variant": "UNKNOWN"},
        "observed_facts": ["Kaputta Kia logosu.", "SUV gövde tipi."],
        "unknowns": [],
        "viral_arastirma_ihtiyaclari": ["Kia Seltos UNKNOWN Türkiye fiyatı ne olur?"],
        "timeline": [],
    }


# DDGS: UNKNOWN'suz sorgulara fiyat + motor hacmi döner (CI logundaki başarılı
# sorguların karşılığı); UNKNOWN'lu sorgulara "No results found".
def _fake_ddgs(sorgu, max_sonuc=4, log_ekle=None, hata_bildir=None):
    if "UNKNOWN" in sorgu:
        return []
    if "fiyat" in sorgu:
        return [{"baslik": "Kia Seltos Türkiye fiyatı", "icerik": "1.6 MPI 1.325.000 TL olarak duyuruldu.", "kaynak": "u1"}]
    if "motor" in sorgu or "hacim" in sorgu:
        return [{"baslik": "Kia Seltos teknik", "icerik": "Motor hacmi 1599 cc, benzinli 1.6 MPI.", "kaynak": "u2"}]
    return [{"baslik": "x", "icerik": "y", "kaynak": "u3"}]


class _ResearchRouter:
    """Fact Lock modeli: web sonuçlarından fiyat ve motor hacmini çıkarır."""

    def metin_uret(self, content, prompt, schema, log, arama_kullan=False, **_):
        fiyat = "1.325.000 TL'den başlıyor (1.6 MPI)" if "1.325.000 TL" in content else ""
        return {
            "facts": [
                {"fact": "Kia Seltos 1.6 MPI benzinli, motor hacmi 1599 cc.", "status": "VERIFIED", "source": "teknik", "source_type": "industry"},
                {"fact": f"Türkiye fiyatı: {fiyat}" if fiyat else "Türkiye fiyatı doğrulanamadı.", "status": "VERIFIED" if fiyat else "UNKNOWN"},
            ],
            "turkiye_satis_durumu": "VAR",
            "turkiye_fiyati": fiyat,
            "global_fiyat_bilgisi": "",
            "turkiye_ilgi_sinyalleri": [],
            "arastirma_notu": "",
        }, "fake-research"


class FiyatMetniTests(unittest.TestCase):
    def test_tl_tutari_cozme(self):
        self.assertEqual([1_325_000], metindeki_tl_tutarlar("Fiyat 1.325.000 TL"))
        self.assertEqual([850_000], metindeki_tl_tutarlar("850 bin TL"))
        self.assertEqual([1_300_000], metindeki_tl_tutarlar("1.3 milyon TL"))
        self.assertEqual([1_325_000], metindeki_tl_tutarlar("1.325.000₺"))
        self.assertEqual([], metindeki_tl_tutarlar("1.6 litre motor"))
        self.assertEqual([], metindeki_tl_tutarlar("25.000 EUR"))
        self.assertEqual([], metindeki_tl_tutarlar(""))

    def test_turkiye_fiyati_alanindan_okunur(self):
        fact = {"turkiye_fiyati": "1.325.000 TL'den başlıyor"}
        self.assertEqual("1.325.000 TL'den başlıyor", turkiye_fiyati_metni(fact))

    def test_olumsuz_ve_yurt_disi_metin_fiyat_sayilmaz(self):
        self.assertEqual("", turkiye_fiyati_metni({"turkiye_fiyati": "Türkiye'de resmi satışı yok"}))
        self.assertEqual("", turkiye_fiyati_metni({"turkiye_fiyati": "Bilinmiyor"}))
        self.assertEqual("", turkiye_fiyati_metni({"global_fiyat_bilgisi": "25.000 USD"}))
        self.assertEqual("", turkiye_fiyati_metni({}))

    def test_verified_factteki_fiyat_yedek_kaynak(self):
        fact = {"turkiye_fiyati": "", "facts": [
            {"fact": "Türkiye fiyatı 1.550.000 TL.", "status": "VERIFIED"},
            {"fact": "Fiyat 2.000.000 TL olur mu?", "status": "INFERENCE"},
        ]}
        self.assertEqual("Türkiye fiyatı 1.550.000 TL.", turkiye_fiyati_metni(fact))

    def test_fiyat_yoksa_talimat_bos(self):
        self.assertEqual("", fiyat_talimati({"turkiye_fiyati": ""}))
        self.assertEqual("", fiyat_talimati({}))

    def test_yurt_disi_fiyat_ve_kur_cevirisi_fiyat_sayilmaz(self):
        self.assertEqual("", turkiye_fiyati_metni({"turkiye_fiyati": "Yurt d\u0131\u015f\u0131nda 900.000 TL kar\u015f\u0131l\u0131\u011f\u0131"}))
        self.assertEqual("", turkiye_fiyati_metni({"turkiye_fiyati": "Avrupa'da 25.000 EUR"}))
        self.assertEqual("", turkiye_fiyati_metni({"facts": [
            {"fact": "Almanya'da 40.000 EUR (yakla\u015f\u0131k 1.400.000 TL).", "status": "VERIFIED"},
        ]}))
        self.assertEqual("", fiyat_talimati({"turkiye_fiyati": "D\u0131\u015f pazarda 1.400.000 TL kar\u015f\u0131l\u0131\u011f\u0131"}))
        # "Euro NCAP" ge\u00e7en me\u015fru T\u00fcrkiye fiyat\u0131 c\u00fcmlesi elenmemeli.
        self.assertEqual(
            "Euro NCAP 5 y\u0131ld\u0131z, fiyat 1.325.000 TL",
            turkiye_fiyati_metni({"turkiye_fiyati": "Euro NCAP 5 y\u0131ld\u0131z, fiyat 1.325.000 TL"}),
        )

    def test_fiyat_varsa_talimat_somut_rakam_ister(self):
        talimat = fiyat_talimati({"turkiye_fiyati": "1.325.000 TL'den başlıyor"})
        self.assertIn("FİYAT KİLİDİ", talimat)
        self.assertIn("1.325.000 TL'den başlıyor", talimat)
        self.assertIn("SOMUT rakam", talimat)


class OtvAralikFiyatTests(unittest.TestCase):
    """ARALIK kilidi fiyat cümlesini ezmez ve bracket'ları saydırmaz."""

    def setUp(self):
        self.kilit = {"durum": "ARALIK", "izinli": [75, 80, 90, 100], "marka": "Kia"}

    def test_kanonik_ifade_iki_ucla_sinirli(self):
        metin = metni_otv_kilidine_cek("ÖTV matrah dilimine göre yüzde 75 veya yüzde 100 olabilir.", self.kilit)
        self.assertIn("%75 ile %100 arasında", metin)
        self.assertNotIn("%80", metin)
        self.assertNotIn("%90", metin)
        self.assertNotIn("matrah dilimine göre matrah", metin)

    def test_tek_yanlis_oran_iki_uca_cekiliyor(self):
        metin = metni_otv_kilidine_cek("Bu araçta ÖTV %75 uygulanıyor.", self.kilit)
        self.assertEqual([75, 100], __import__("core.otv_kilidi", fromlist=["x"]).metindeki_otv_oranlari(metin))

    def test_fiyat_cumlesi_kilitten_etkilenmez(self):
        metin = metni_otv_kilidine_cek("Fiyatı 1.325.000 TL'den başlıyor.", self.kilit)
        self.assertEqual("Fiyatı 1.325.000 TL'den başlıyor.", metin)

    def test_seslendirme_kilitle_fiyati_korur(self):
        script = {
            "segments": [{"speaker": "female", "tts_tag": "", "text": (
                "Kia Seltos Türkiye'ye geldi. Fiyatı 1.325.000 TL'den başlıyor. "
                "ÖTV matrah dilimine göre yüzde 75 veya yüzde 100 olabilir."
            )}],
            "yorum_tetikleyici_soru": "Sizce bu fiyat hak mı?",
        }
        yeni = senaryoyu_otv_kilidine_cek(script, {"vergi_kilidi": self.kilit}, lambda *_: None)
        metin = yeni["segments"][0]["text"]
        self.assertIn("1.325.000 TL", metin)
        self.assertIn("%75 ile %100 arasında", metin)
        self.assertNotIn("matrah dilimine göre matrah", metin)


class MatrahKilidiTests(unittest.TestCase):
    def test_acikca_yazilmis_matrah_tek_orana_kilitler(self):
        fact = {
            "facts": [
                {"fact": "Kia Seltos 1.6 MPI benzinli, motor hacmi 1599 cc.", "status": "VERIFIED"},
                {"fact": "Aracın ÖTV matrahı 1.000.000 TL olarak hesaplandı.", "status": "VERIFIED"},
            ],
        }
        kilit = otv_kilidi_hesapla(fact, "", {"video_identity": {"brand": "Kia", "exact_model": "Seltos"}})
        self.assertEqual("KESIN", kilit["durum"])
        self.assertEqual(80, kilit["oran"])
        self.assertEqual(1_000_000, kilit["matrah_tl"])

    def test_tablo_esik_degeri_matrah_sayilmaz(self):
        """'matrahı 850.000 TL'ye kadar olanlar %75' gibi tablo metni matrah değildir."""
        fact = {"facts": [{"fact": "Kia Seltos 1.6 benzinli, 1599 cc.", "status": "VERIFIED"}]}
        web = "1400-1600 cc bandında matrahı 850.000 TL'ye kadar olanlar %75, 1.100.000 TL'ye kadar %80."
        kilit = otv_kilidi_hesapla(fact, web, {"video_identity": {"brand": "Kia", "exact_model": "Seltos"}})
        self.assertIsNone(kilit["matrah_tl"])
        self.assertEqual("ARALIK", kilit["durum"])

    def test_satis_fiyati_matrah_sayilmaz(self):
        """Satış fiyatı vergi matrahı değildir; fiyat bilgisi kilidi ARALIK bırakır."""
        fact = {
            "facts": [{"fact": "Kia Seltos 1.6 benzinli, motor hacmi 1599 cc.", "status": "VERIFIED"}],
            "turkiye_fiyati": "1.325.000 TL'den başlıyor",
        }
        kilit = otv_kilidi_hesapla(fact, "", {"video_identity": {"brand": "Kia", "exact_model": "Seltos"}})
        self.assertIsNone(kilit["matrah_tl"])
        self.assertEqual("ARALIK", kilit["durum"])


class UctanUcaFiyatAkisiTests(unittest.TestCase):
    """CI logundaki senaryonun tamamı: arama → fact lock → seslendirme kilidi."""

    def test_fiyat_verisi_fact_locka_ve_seslendirmeye_ulasir(self):
        logs = []
        with patch.object(web_search, "duckduckgo_sorgu", side_effect=_fake_ddgs):
            state, _ = _research_calistir(_ResearchRouter(), _video_state(), logs.append)
        # 1) Fiyat Fact Lock'a girdi.
        self.assertEqual("1.325.000 TL'den başlıyor (1.6 MPI)", state["turkiye_fiyati"])
        # 2) Fiyat talimatı üretiliyor (içerik üretimi fiyatı somut rakamla verir).
        self.assertIn("1.325.000 TL", fiyat_talimati(state))
        # 3) Sorgularda UNKNOWN kalmadı.
        for sorgu in web_search.arastirma_sorgulari_olustur(_video_state()):
            self.assertNotIn("UNKNOWN", sorgu.upper())
        # 4) Seslendirme kilidi fiyatı korur.
        script = {
            "segments": [{"speaker": "female", "tts_tag": "", "text": (
                "Kia Seltos Türkiye'ye geldi, fiyatı 1.325.000 TL'den başlıyor."
            )}],
            "yorum_tetikleyici_soru": "Sizce bu fiyat hak mı?",
        }
        yeni = senaryoyu_otv_kilidine_cek(script, state, logs.append)
        self.assertIn("1.325.000 TL", yeni["segments"][0]["text"])


class _PromptYakalayanRouter:
    """Ajan promptlarını yakalar; gerçekçi sahte çıktı döner."""

    def __init__(self):
        self.promptlar = {}

    def metin_uret(self, content, prompt, schema, log, arama_kullan=False, **_):
        anahtar = prompt[:40]
        self.promptlar[anahtar] = prompt
        if "Sohbet Yazarısın" in prompt:
            return {
                "segments": [{"speaker": "female", "tts_tag": "[vurgulu]", "text": (
                    "Kia Seltos Türkiye'ye geldi. Fiyatı 1.325.000 TL'den başlıyor. "
                    "Bu fiyat bandında rakiplerine meydan okuyor."
                )}],
                "yorum_tetikleyici_soru": "Sizce bu fiyat hak mı?",
            }, "fake-script"
        if "CAPTION" in prompt or "BAŞLIK (reels_baslik)" in prompt:
            return {
                "reels_baslik": "KIA SELTOS TÜRKİYE'DE",
                "reels_aciklama": (
                    "Kia Seltos Türkiye pazarına girişti. 1.6 MPI motor 1.325.000 TL, "
                    "hibrit versiyon 1.550.000 TL olarak duyuruldu."
                ),
                "reels_hashtag": ["kia", "seltos", "otomobil", "suv", "turkiye"],
            }, "fake-metadata"
        return {"x": 1}, "fake-diger"

    def ses_uret(self, text, voice, output, log, hiz_carpani=1.0, **kwargs):
        Path(output).write_bytes(b"fake-audio")
        return True, "fake-tts"


class IcerikUretimFiyatTests(unittest.TestCase):
    """Fiyat talimatı Script Writer ve Metadata ajanlarına ulaşır; üretim korunur."""

    def _fact_state(self):
        return {
            "facts": [
                {"fact": "Kia Seltos 1.6 MPI benzinli, motor hacmi 1599 cc.", "status": "VERIFIED"},
                {"fact": "Türkiye fiyatı: 1.325.000 TL'den başlıyor (1.6 MPI)", "status": "VERIFIED"},
            ],
            "turkiye_satis_durumu": "VAR",
            "turkiye_fiyati": "1.325.000 TL'den başlıyor (1.6 MPI)",
            "turkiye_ilgi_sinyalleri": [],
        }

    def test_script_writer_promptu_fiyat_kilidini_tasir(self):
        from core.agentic import _script_writer_calistir
        router = _PromptYakalayanRouter()
        logs = []
        _script_writer_calistir(
            router, {}, {}, self._fact_state(), {}, {}, 7.0, logs.append,
            mod="SOLO_FEMALE",
        )
        prompt = next(p for p in router.promptlar.values() if "Sohbet Yazarısın" in p)
        self.assertIn("FİYAT KİLİDİ", prompt)
        self.assertIn("1.325.000 TL'den başlıyor", prompt)

    def test_metadata_promptu_fiyat_kilidini_tasir(self):
        from core.agentic import _metadata_gen_calistir
        router = _PromptYakalayanRouter()
        _metadata_gen_calistir(router, {}, {}, self._fact_state(), lambda m: None, ton="dengeli")
        prompt = next(p for p in router.promptlar.values() if "BAŞLIK (reels_baslik)" in p)
        self.assertIn("FİYAT KİLİDİ", prompt)
        self.assertIn("1.325.000 TL", prompt)

    def test_otv_aralik_talimati_kisa_formatta_sessizligi_ister(self):
        from core.otv_kilidi import vergi_kilidi_talimati
        fact = self._fact_state()
        fact["vergi_kilidi"] = {"durum": "ARALIK", "izinli": [75, 80, 90, 100]}
        talimat = vergi_kilidi_talimati(fact)
        self.assertIn("HİÇ SÖYLEME", talimat)
        self.assertIn("doğrulanmış fiyat", talimat)

    def test_fiyat_odakli_senaryo_kilitten_etkilenmez(self):
        from core.agentic import _script_writer_calistir
        from core.otv_kilidi import vergi_kilidini_uygula
        router = _PromptYakalayanRouter()
        fact_state = vergi_kilidini_uygula(
            self._fact_state(),
            otv_kilidi_hesapla(self._fact_state(), "", {"video_identity": {"brand": "Kia", "exact_model": "Seltos"}}),
            lambda *_: None,
        )
        script, _ = _script_writer_calistir(
            router, {}, {}, fact_state, {}, {}, 7.0, lambda m: None, mod="SOLO_FEMALE",
        )
        kilitli = senaryoyu_otv_kilidine_cek(script, fact_state, lambda *_: None)
        metin = kilitli["segments"][0]["text"]
        # Fiyat korunur, seslendirme vergi bracket'larına dolmaz.
        self.assertIn("1.325.000 TL", metin)
        self.assertNotIn("%80", metin)
        self.assertNotIn("%90", metin)


if __name__ == "__main__":
    unittest.main()
