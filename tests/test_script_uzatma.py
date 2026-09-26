"""Senaryo uzatma stratejisi regresyon testleri (Eylül 2026 üretim logu).

Kök neden: 123 sn'lik videoda 355 kelime hedefi vardı; Script Writer 3 tam
yazımda 155/150/160 kelime üretti, TTS 57 sn'lik kaldı, FFmpeg 1.5x hızlandırmaya
rağmen videonun sonu ~25 sn sessiz bitti. "Sıfırdan daha uzun yaz" talimatı
işe yaramadığı için kısa senaryo artık mevcut metni BİREBİR koruyan somut bir
UZATMA göreviyle düzeltilir; geçersiz uzatma (açılış kaybolma/kısalma) önceki
senaryoyu korur.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.agentic import (
    _kelime_sayisi, _script_writer_calistir, _senaryo_metni, _uzatma_gecerli_mi,
    agentic_icerik_uretimi,
)

# 120 sn video → hedef 350, izin verilen 315-385 kelime.
SURE = 120


class _Router:
    """Sırayla yanıt veren router; promptları da kaydeder."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.prompts = []

    def metin_uret(self, *args, **kwargs):
        self.prompts.append(args[1] if len(args) > 1 else kwargs.get("prompt", ""))
        response = self.responses[self.calls % len(self.responses)]
        self.calls += 1
        return response, f"fake-model-{self.calls}"

    def ses_uret(self, text, voice, output, log, hiz_carpani=1.0):
        Path(output).write_bytes(b"fake-audio-data")
        return True, "fake-tts-model"


def _detective():
    return {
        "kronik_sikayetler": ["Yakıt tüketimi yüksek"],
        "turkiye_ozel_magduriyet": "ÖTV dilimi dezavantajı",
        "viral_kan_mali": "Türkiye fiyatı Avrupa'nın 2 katı",
    }


def _hook():
    return {
        "secilen_sablon": "Ters_Kose",
        "kapak_metni": "Fiyat Şoku",
        "ilk_3_saniye_kanca": "Bu fiyat herkesi şaşırttı",
        "kapak_basliklari": [],
    }


def _critic(score=8, approved=True, feedback=""):
    return {"score": score, "approved": approved, "feedback": feedback}


def _metadata():
    return {
        "reels_baslik": "Fiyat Şoku",
        "reels_aciklama": "Bu fiyat herkesi şaşırttı",
        "reels_hashtag": ["#otomobil"],
    }


def _kisali_script():
    return {
        "segments": [
            {"speaker": "female", "tts_tag": "[şaşırarak]", "text": "Bu Lexus ES 300h Türkiye'ye geliyor diyorlar."},
            {"speaker": "male", "tts_tag": "", "text": "Ama fiyat hâlâ netleşmedi, ÖTV belirsiz."},
        ],
        "yorum_tetikleyici_soru": "Siz bu fiyata alırdınız mı?",
    }


def _dolgu_satiri(i):
    return f"Olay {i} numaralı detay burada önemli: fiyat bandında kullanıcılar servis ağı ve ikinci el değeri tartışıyor, bu da seçimi zorlaştırıyor."


def _uzatmis_script(satir_sayisi=15):
    """Kısa senaryonun BİREBİR korunması + ek replikler (toplam 315-385 aralığında)."""
    base = _kisali_script()
    segments = [dict(s) for s in base["segments"]]
    speaker = "male"
    for i in range(1, satir_sayisi + 1):
        speaker = "female" if speaker == "male" else "male"
        segments.append({"speaker": speaker, "tts_tag": "[vurgulu]" if i % 3 == 0 else "", "text": _dolgu_satiri(i)})
    return {"segments": segments, "yorum_tetikleyici_soru": base["yorum_tetikleyici_soru"]}


def _gecersiz_uzatma(satir_sayisi=20):
    """Yalnızca YENİ ekler: eski açılış içermez (modelin TAM senaryo döndürmemesi)."""
    segments = []
    speaker = "male"
    for i in range(1, satir_sayisi + 1):
        speaker = "female" if speaker == "male" else "male"
        segments.append({"speaker": speaker, "tts_tag": "", "text": _dolgu_satiri(i)})
    return {"segments": segments, "yorum_tetikleyici_soru": "Siz bu fiyata alırdınız mı?"}


def _uzun_script():
    """385+ kelimelik, aralığın ÜSTÜNDE senaryo (kısaltma yolu için)."""
    segments = []
    speaker = "female"
    for i in range(1, 23):
        speaker = "female" if speaker == "male" else "male"
        segments.append({"speaker": speaker, "tts_tag": "", "text": _dolgu_satiri(i)})
    return {"segments": segments, "yorum_tetikleyici_soru": "Siz bu fiyata alırdınız mı?"}


def _mock_duo_ses(router, segments, output_path, log, hiz_carpani=1.0):
    Path(output_path).write_bytes(b"fake-duo-audio")
    return True, "fake-duo-tts"


class UzatmaStratejiTestleri(unittest.TestCase):

    def _calistir(self, responses):
        router = _Router(responses)
        logs = []
        tts_cagrilari = {"n": 0}

        def _duo(router_, segments, output_path, log, hiz_carpani=1.0):
            tts_cagrilari["n"] += 1
            return _mock_duo_ses(router_, segments, output_path, log, hiz_carpani)

        with patch("core.agentic._ses_suresini_al", return_value=float(SURE)), \
             patch("core.agentic.duo_ses_uret", side_effect=_duo):
            reels, model, plan, script, ses_ok, ses_model, ses_modu, ses_dosyasi, meta = agentic_icerik_uretimi(
                router, {}, {}, {}, SURE, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "DUO"}
            )
        return reels, script, logs, router, tts_cagrilari["n"]

    def test_kisali_script_uzatmayla_hedeferlasiyor(self):
        """Kısa senaryo → mevcut metni koruyan uzatma; geçerli uzatma kabul edilir, TTS uzatılmış senaryoyla üretilir."""
        reels, script, logs, router, tts_n = self._calistir([
            _detective(), _hook(), _kisali_script(), _critic(),
            _gecersiz_uzatma(), _uzatmis_script(), _critic(), _metadata(),
        ])
        # Uzatma stratejisi loglandı ve geçersiz ilk deneme korunarak ikinci denemeye geçildi.
        self.assertTrue(any("mevcut senaryo KORUNARAK yeni repliklerle uzatılıyor" in l for l in logs))
        self.assertTrue(any("Uzatma üretimi geçersiz" in l for l in logs))
        # Uzatma promptu mevcut senaryoyu birebir koruma + uzatma görevini içeriyor.
        uzatma_prompts = [p for p in router.prompts if "SENARYO UZATMA" in p]
        self.assertEqual(len(uzatma_prompts), 2)
        self.assertIn("MEVCUT SENARYO (birebir koru)", uzatma_prompts[0])
        self.assertIn("Bu Lexus ES 300h Türkiye'ye geliyor diyorlar", uzatma_prompts[0])
        self.assertIn("birebir", uzatma_prompts[0])
        # Son senaryo uzatılmış hâli: TTS de uzatılmış senaryoyla bir kez üretildi.
        self.assertEqual(len(script["segments"]), 18)  # 17 segment + kapanış sorusu
        self.assertEqual(tts_n, 1)
        # Kelime kontrolü aralıkta bitti.
        son_kontrol = [l for l in logs if "uzunluk kontrolü" in l]
        self.assertTrue(son_kontrol, "son kelime kontrolü loglanmalı")
        adet = int(son_kontrol[-1].split(":")[1].split("kelime")[0])
        self.assertTrue(315 <= adet <= 385, f"adet {adet} 315-385 aralığında olmalı")
        # Uzatılmış senaryonun gerçek kelime sayısı da aralıkta olmalı.
        son_senaryo = {"segments": script["segments"][:-1], "yorum_tetikleyici_soru": ""}
        self.assertTrue(315 <= _kelime_sayisi(_senaryo_metni(son_senaryo)) <= 385)
        # Ve kısaltılmış orijinal senaryo aralığın BELİRGİN altında başlıyordu.
        self.assertLess(_kelime_sayisi(_senaryo_metni(_kisali_script())), 315)

    def test_tum_uzatmalar_gecersizse_onceki_senaryo_tssle_devam(self):
        """İki uzatma da açılışı korumazsa: en iyi mevcut (orijinal) senaryo TTS'e gider, üretim ölmez."""
        reels, script, logs, router, tts_n = self._calistir([
            _detective(), _hook(), _kisali_script(), _critic(),
            _gecersiz_uzatma(), _gecersiz_uzatma(satir_sayisi=18), _metadata(),
        ])
        self.assertTrue(any("Uzatma üretimi geçersiz" in l for l in logs))
        self.assertTrue(any("hala düzeltilemedi" in l for l in logs))
        self.assertTrue(any("en iyi mevcut senaryo ile devam" in l for l in logs))
        # TTS orijinal kısa senaryoyla (2 segment + soru) yine de üretildi.
        self.assertEqual(tts_n, 1)
        self.assertEqual(len(script["segments"]), 3)

    def test_uzun_script_kisaltma_ile_yeniden_yazilir(self):
        """Aralığın üstünde kalan senaryo 'ÇOK UZUN' talimatıyla yeniden yazar (uzatma kullanılmaz)."""
        reels, script, logs, router, tts_n = self._calistir([
            _detective(), _hook(), _uzun_script(), _critic(),
            _uzun_script(), _critic(),
            _uzun_script(), _critic(),
            _metadata(),
        ])
        self.assertTrue(any("ÇOK UZUN" in p for p in router.prompts), "ikinci yazımda kısaltma talimatı olmalı")
        self.assertFalse(any("SENARYO UZATMA" in p for p in router.prompts), "uzun senaryoda uzatma stratejisi kullanılmaz")
        self.assertTrue(any("hala düzeltilemedi" in l for l in logs))
        self.assertEqual(tts_n, 1)

    def test_ilk_prompt_sure_soylesmesi_ve_segment_butcesi_icerir(self):
        """İlk yazım promptu somut süre sözleşmesi + segment bütçesi taşır (boş 'doğal hız' ifadesi yok)."""
        router = _Router([_kisali_script()])
        logs = []
        _script_writer_calistir(
            router, _hook(), _detective(), {}, {}, {}, SURE, logs.append,
            hedef_kelime_bilgisi="Hedef 350 kelime. Kesin aralık 315-385 kelime.",
            mod="DUO", hedef_kelime=350,
            segment_butcesi="SEGMENT BÜTÇESİ: Senaryoyu 10-16 replikte kur; her replik ortalama 20-35 kelime olsun. DUO modunda iki konuşmacının kelimeleri birlikte bu bütçeyi oluşturur.",
        )
        prompt = router.prompts[0]
        self.assertIn("SÜRE SÖZLEŞMESİ", prompt)
        self.assertIn("Kısa senaryo YASAK", prompt)
        self.assertIn("SEGMENT BÜTÇESİ", prompt)
        self.assertIn("10-16 replikte", prompt)
        self.assertIn("Hedef 350 kelime. Kesin aralık 315-385 kelime.", prompt)
        self.assertNotIn("video süresine uygun doğal konuşma hızı", prompt)


class UzatmaGecerlilikTestleri(unittest.TestCase):
    def test_kisalmada_gecersiz(self):
        self.assertFalse(_uzatma_gecerli_mi("a b c d e", "a b c", 5, 3))

    def test_acilis_korunmayinda_gecersiz(self):
        eski = "Bu Lexus ES 300h Türkiye'ye geliyor diyorlar. Ama fiyat hâlâ netleşmedi."
        yeni = "Kâğıt üzerinde farklı bir senaryo; eski açılış yok. " + "kelime " * 40
        self.assertFalse(_uzatma_gecerli_mi(eski, yeni, 12, 60))

    def test_acilis_korundugunda_gecerli(self):
        eski = "Bu Lexus ES 300h Türkiye'ye geliyor diyorlar. Ama fiyat hâlâ netleşmedi."
        yeni = eski + " " + "yeni replik kelimeleri ekleniyor " * 20
        self.assertTrue(_uzatma_gecerli_mi(eski, yeni, 12, 92))

    def test_tts_etiketi_normalizasyonu(self):
        eski = "[şaşırarak] Bu Lexus ES 300h Türkiye'ye geliyor diyorlar."
        yeni = "Bu Lexus ES 300h Türkiye'ye geliyor diyorlar. " + "ek replik " * 30
        self.assertTrue(_uzatma_gecerli_mi(eski, yeni, 8, 68))

    def test_eski_metin_bossa_sadece_uzunluk_bakar(self):
        self.assertTrue(_uzatma_gecerli_mi("", "bir şeyler var", 0, 3))


if __name__ == "__main__":
    unittest.main()
