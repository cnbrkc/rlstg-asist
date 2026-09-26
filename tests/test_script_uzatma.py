"""Senaryo uzatma stratejisi regresyon testleri (Eylül 2026 üretim logu, 2. tur).

Kök neden: 123 sn'lik videoda 355 kelime hedefi vardı; Script Writer 3 tam
yazımda 155/150/160 kelime üretti, TTS 57 sn'lik kaldı, FFmpeg 1.5x hızlandırmaya
rağmen videonun sonu ~25 sn sessiz bitti. "Sıfırdan daha uzun yaz" talimatı
işe yaramadığı için kısa senaryo artık mevcut metni BİREBİR koruyan somut bir
UZATMA göreviyle düzeltilir; geçersiz uzatma önceki senaryoyu korur.

2. tur (akış ve tekrar koruması):
- Kapanış sorusu yalnız `yorum_tetikleyici_soru` alanındadır; replik kopyası
  `_kapanis_sorusunu_ayristir` ile atılır → uzatmada soru metnin ortasına
  gömülmez, seslendirme soruyu iki kez okumaz.
- Uzatma kabul koşulları: uzunluk + açılış + TÜM eski replikler aynı sırada
  birebir (kopuk ikinci senaryo engeli) + mükerrer replik yok (token Jaccard).
- Uzunluk bütçesi cümle-tabanlı: flash serisi kelime sayamaz, cümle sayar.
"""
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.agentic import (
    _kelime_sayisi, _script_writer_calistir, _segment_butcesi_olustur,
    _senaryo_metni, _uzatma_gecerli_mi, _kapanis_sorusunu_ayristir,
    _tekrarli_replik_var_mi, _ek_replikler_bul, agentic_icerik_uretimi,
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

    def ses_uret(self, text, voice, output, log, hiz_carpani=1.0, **kwargs):
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


def _kisali_script_sorulu():
    """Kapanış sorusu AYNI ZAMANDA son replik olarak da yazılmış kısa senaryo.
    (Kötü üretim durumu: uzatmada soru metnin ortasına gömülürdü.)"""
    script = _kisali_script()
    script["segments"] = [dict(s) for s in script["segments"]] + [
        {"speaker": "female", "tts_tag": "[vurgulu]", "text": "Siz bu fiyata alırdınız mı?"},
    ]
    return script


# Mükerrer replik engelini geçecek ölçüde BİRBİRİNDEN FARKLI uzatma cümleleri
# (en yüksek token Jaccard ~0.06). Her biri kanıt havuzundan beslenir gibi
# bağımsız bir bilgi taşır.
_DOLGU_SATIRLARI = [
    "Servis ağı İstanbul ve Ankara dışındaki illerde henüz tam kapasiteye ulaşmadı, bu nedenle ilk bakımlar için uzun mesafe ve randevu bekleme düşünmek gerekiyor.",
    "İkinci el değeri hibrit araçlarda bozuluyor çünkü batarya sağlık raporu isteniyor, bu rapor pahalıya çıkıyor ve pazarlık gücünü doğrudan aşağı çekiyor.",
    "Avrupa'daki liste fiyatıyla Türkiye'deki fiyat arasındaki fark, vergi dilimi yüzünden on binlerce liraya kadar açılıyor ve bu fark her ay yeniden hesaplanıyor.",
    "Yetkili satıcılar teslimat tarihini garanti etmiyor, beklenen süre üç ayı bulabiliyor ve bu belirsizlik yüzünden alıcılar ikinci el piyasasına yöneliyor.",
    "Günlük şehir içi kullanımda sarf yakıt tüketimi dizel rakibinin yarısına iniyor, uzun yolda ise bu fark belirgin şekilde daralıyor ve maliyet dengeleniyor.",
    "Garanti paketi standart on yıl olarak açıklanıyor ama batarya garantisi ayrı şartlara bağlanıyor, bu maddeyi satıcıya okumadan sözleşmeye imza atmak riskli.",
    "Rakip modelin donanım seviyeleri arasında arka koltuk ısıtması ikinci pakete konmuş, bu da giriş seviye fiyatının cazibesini belirgin şekilde zayıflatıyor.",
    "Sigorta primi hasar geçmişi temiz sürücülerde beklentinin altında kaldı, kasko ise seçilen ek teminatlara göre ciddi değişiyor ve sürücü profilini sorguluyor.",
    "Yazılım güncellemesi sesli asistanın Türkçe tanıma kalitesini belirgin artırmış, menü navigasyonu hâlâ beklentinin gerisinde kalıyor ve öğrenme süresi istiyor.",
    "Kiralık araç filolarında modelin tüketim verisi sayesinde aylık maliyet benzinli rakibine kıyasla ciddi oranda düşüyor, operasyoncular bunu bilançoya yansıtıyor.",
    "Fabrika çıkışı jant boyutu opsiyonel olarak genişletilebiliyor, buna rağmen lastik değişim maliyeti segment ortalamasının üzerinde kalıyor ve bütçeyi zorluyor.",
    "Yedek parça envanteri ilk iki yıl yurt içi depoya taşınacak, bu süre içinde özel parça için yurt dışından bekleme yaşanabilir ve araç parkta kalıyor.",
    "Şarj soketi ev tipi prizle uyumlu, ancak gece boyunca şarj süresi tam dolu hâlden sekiz saati bulabiliyor ve günlük planı etkiliyor.",
    "Aracın bagaj hacmi koltuklar katlandığında rakibini geçiyor, elektrikli sürgünün gecikmeli açılması ise kullanıcı şikâyetlerinin başında geliyor ve şube ilgileniyor.",
    "Ödeme planlarında peşinat oranı düşük tutulsa da vade uzadıkça toplam maliyet artışı, nakit fiyat farkını tamamen yutabiliyor ve faiz yükünü büyütüyor.",
]


def _uzatmis_script(satir_sayisi=15):
    """Kısa senaryonun BİREBİR korunması + farklı yeni replikler (315-385 aralığında)."""
    base = _kisali_script()
    segments = [dict(s) for s in base["segments"]]
    speaker = "male"
    for i in range(satir_sayisi):
        speaker = "female" if speaker == "male" else "male"
        segments.append({
            "speaker": speaker,
            "tts_tag": "[vurgulu]" if i % 3 == 0 else "",
            "text": _DOLGU_SATIRLARI[i % len(_DOLGU_SATIRLARI)],
        })
    return {"segments": segments, "yorum_tetikleyici_soru": base["yorum_tetikleyici_soru"]}


def _gecersiz_uzatma(satir_sayisi=20):
    """Yalnızca YENİ ekler: eski açılış içermez (modelin TAM senaryo döndürmemesi)."""
    segments = []
    speaker = "male"
    for i in range(satir_sayisi):
        speaker = "female" if speaker == "male" else "male"
        segments.append({"speaker": speaker, "tts_tag": "", "text": _DOLGU_SATIRLARI[i % len(_DOLGU_SATIRLARI)]})
    return {"segments": segments, "yorum_tetikleyici_soru": "Siz bu fiyata alırdınız mı?"}


def _uzun_script():
    """385+ kelimelik, aralığın ÜSTÜNDE senaryo (kısaltma yolu için)."""
    segments = []
    speaker = "female"
    for i in range(2 * len(_DOLGU_SATIRLARI)):
        speaker = "female" if speaker == "male" else "male"
        segments.append({"speaker": speaker, "tts_tag": "", "text": _DOLGU_SATIRLARI[i % len(_DOLGU_SATIRLARI)]})
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
        # Uzatma promptu mevcut senaryoyu birebir koruma + cümle-tabanlı uzatma görevini içeriyor.
        uzatma_prompts = [p for p in router.prompts if "SENARYO UZATMA" in p]
        self.assertEqual(len(uzatma_prompts), 2)
        self.assertIn("MEVCUT SENARYO (birebir koru)", uzatma_prompts[0])
        self.assertIn("Bu Lexus ES 300h Türkiye'ye geliyor diyorlar", uzatma_prompts[0])
        self.assertIn("birebir", uzatma_prompts[0])
        self.assertIn("YENİ CÜMLE", uzatma_prompts[0])
        self.assertIn("MÜKERRETTİR", uzatma_prompts[0])
        self.assertIn("Kapanış sorusu REPLİK DEĞİLDİR", uzatma_prompts[0])
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

    def test_kapanis_sorusu_replikteyken_uzatmada_ortada_kalmayacagini_gosterir(self):
        """Kapanış sorusu son replik olarak da yazılmışsa: uzatma girdisinden
        çıkar, seslendirme soruyu TAM BİR KEZ ve EN SONDA okur."""
        reels, script, logs, router, tts_n = self._calistir([
            _detective(), _hook(), _kisali_script_sorulu(), _critic(),
            _gecersiz_uzatma(), _uzatmis_script(), _critic(), _metadata(),
        ])
        self.assertTrue(any("🧹 Kapanış sorusu replik listesinden çıkarıldı" in l for l in logs))
        uzatma_prompt = [p for p in router.prompts if "SENARYO UZATMA" in p][0]
        blok = uzatma_prompt.split("MEVCUT SENARYO (birebir koru):", 1)[1]
        blok = blok.split("YORUM SORUSU", 1)[0]
        numarali = [satir for satir in blok.strip().splitlines() if re.match(r"^\d+\. ", satir)]
        self.assertEqual(len(numarali), 2, "uzatma girdisinde yalnız gövde replikleri olmalı")
        self.assertNotIn("Siz bu fiyata alırdınız mı?", blok)
        # Seslendirme metni: soru tam bir kez ve en sonda.
        metin = reels["seslendirme_metni"]
        self.assertEqual(metin.count("Siz bu fiyata alırdınız mı?"), 1)
        self.assertTrue(metin.rstrip().endswith("Siz bu fiyata alırdınız mı?"))
        self.assertEqual(tts_n, 1)

    def test_uzatmada_kopya_soru_repligi_atilir(self):
        """Model uzatmada kapanış sorusunu son replik olarak YENİDEN eklerse
        replik kopyası kod tarafında atılır; kapanış yine tek ve en sonda kalır."""
        def _sorulu_uzatma():
            script = _uzatmis_script()
            script["segments"] = [dict(s) for s in script["segments"]] + [
                {"speaker": "male", "tts_tag": "[vurgulu]", "text": "Siz bu fiyata alırdınız mı?"},
            ]
            return script

        reels, script, logs, router, tts_n = self._calistir([
            _detective(), _hook(), _kisali_script(), _critic(),
            _gecersiz_uzatma(), _sorulu_uzatma(), _critic(), _metadata(),
        ])
        metin = reels["seslendirme_metni"]
        self.assertEqual(metin.count("Siz bu fiyata alırdınız mı?"), 1, "soru iki kez okunmamalı")
        self.assertTrue(metin.rstrip().endswith("Siz bu fiyata alırdınız mı?"))
        # 17 gövde segment + 1 alan sorusu repliği
        self.assertEqual(len(script["segments"]), 18)

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

    def test_ilk_prompt_sure_soylesmesi_ve_cumle_butcesi_icerir(self):
        """İlk yazım promptu somut süre sözleşmesi + CÜMLE-TABANLI bütçe taşır;
        kapanış sorusu yalnız alan değil replik olarak istenmez."""
        router = _Router([_kisali_script()])
        logs = []
        butce = _segment_butcesi_olustur(350, 315, 385, "DUO")
        _script_writer_calistir(
            router, _hook(), _detective(), {}, {}, {}, SURE, logs.append,
            hedef_kelime_bilgisi="Hedef 350 kelime. Kesin aralık 315-385 kelime.",
            mod="DUO", hedef_kelime=350, segment_butcesi=butce,
        )
        prompt = router.prompts[0]
        self.assertIn("SÜRE SÖZLEŞMESİ", prompt)
        self.assertIn("Kısa senaryo YASAK", prompt)
        self.assertIn("CÜMLE TABANLI", prompt)
        self.assertIn("TAM 32 CÜMLE", prompt)
        self.assertIn("TEK TEK SAY", prompt)
        self.assertIn("Hedef 350 kelime. Kesin aralık 315-385 kelime.", prompt)
        self.assertNotIn("video süresine uygun doğal konuşma hızı", prompt)
        # Soru yalnız alana yazılır; segments'e soru cümlesi koymak yasak.
        self.assertIn("yorum_tetikleyici_soru alanına yaz", prompt)
        self.assertIn("soru cümlesi KOYMA", prompt)


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

    # --- 2. tur: tüm eski replikler aynı sırada birebir (kopuk senaryo engeli) ---
    def test_oradaki_eski_replik_eksikse_gecersiz(self):
        eski = ("Bu araç Türkiye'ye bu ay geliyor diyorlar, fiyat tablosu henüz açıklanmadı. "
                "Motor seçeneği yalnız hibrit ve batarya paketi Avrupa versiyonuyla aynı. "
                "Kapanış cümlesi burada duruyor.")
        yeni = eski.replace("Motor seçeneği yalnız hibrit ve batarya paketi Avrupa versiyonuyla aynı. ", "") + " " + "ek içerik " * 20
        parcalar = [
            "Bu araç Türkiye'ye bu ay geliyor diyorlar, fiyat tablosu henüz açıklanmadı.",
            "Motor seçeneği yalnız hibrit ve batarya paketi Avrupa versiyonuyla aynı.",
            "Kapanış cümlesi burada duruyor.",
        ]
        self.assertFalse(_uzatma_gecerli_mi(eski, yeni, 15, 45, eski_parcalar=parcalar))

    def test_eski_replikler_sirayla_korunursa_gecerli(self):
        eski = ("Bu araç Türkiye'ye bu ay geliyor diyorlar, fiyat tablosu henüz açıklanmadı. "
                "Motor seçeneği yalnız hibrit ve batarya paketi Avrupa versiyonuyla aynı. "
                "Kapanış cümlesi burada duruyor.")
        yeni = eski + " " + "ek içerik " * 20
        parcalar = [
            "Bu araç Türkiye'ye bu ay geliyor diyorlar, fiyat tablosu henüz açıklanmadı.",
            "Motor seçeneği yalnız hibrit ve batarya paketi Avrupa versiyonuyla aynı.",
            "Kapanış cümlesi burada duruyor.",
        ]
        self.assertTrue(_uzatma_gecerli_mi(eski, yeni, 15, 45, eski_parcalar=parcalar))

    def test_eski_replikler_tersten_dizilirse_gecersiz(self):
        p1 = "Bu araç Türkiye'ye bu ay geliyor diyorlar, fiyat tablosu henüz açıklanmadı."
        p2 = "Motor seçeneği yalnız hibrit ve batarya paketi Avrupa versiyonuyla aynı."
        p3 = "Kapanış cümlesi burada duruyor."
        eski = f"{p1} {p2} {p3}"
        # Açılış (ilk 60 karakter) korunur ama ortadaki replikler yer değiştirdi.
        yeni = f"{p1} {p3} {p2} " + "ek içerik " * 20
        self.assertFalse(_uzatma_gecerli_mi(eski, yeni, 15, 45, eski_parcalar=[p1, p2, p3]))


class TekrarKorumaTestleri(unittest.TestCase):
    def test_korunan_eski_replikler_kontrol_disi_kalir(self):
        """Uzatma çıktısındaki birebir korunan eski replikler 'mükerrer'
        sayılmaz; mükerrerlik kontrolü yalnız EKLENTİ repliklere uygulanır."""
        eski = [
            {"text": "Bu Lexus ES 300h Türkiye'ye geliyor diyorlar."},
            {"text": "Ama fiyat hâlâ netleşmedi, ÖTV belirsiz."},
        ]
        yeni = [
            {"text": "[şaşırarak] Bu Lexus ES 300h Türkiye'ye geliyor diyorlar."},
            {"text": "Ama fiyat hâlâ netleşmedi, ÖTV belirsiz."},
            {"text": "Yeni bilgi: batarya paketi Avrupa versiyonuyla aynı kalıyor."},
        ]
        ek = _ek_replikler_bul(eski, yeni)
        self.assertEqual(len(ek), 1)
        self.assertIn("batarya paketi", ek[0]["text"])
        self.assertFalse(_tekrarli_replik_var_mi(eski, ek))

    def test_kopya_replik_tespit_edilir(self):
        eski = [{"text": "Fiyat hâlâ netleşmedi, ÖTV belirsiz."}]
        yeni = [{"text": "Fiyat hâlâ netleşmedi, ÖTV belirsiz görünüyor."}]
        self.assertTrue(_tekrarli_replik_var_mi(eski, yeni))

    def test_yeni_replikler_arsinda_kopya_tespit_edilir(self):
        eski = [{"text": "Giriş repliği: fiyat tablosu açıklanmadı."}]
        c = "Batarya sağlık raporu isteniyor ve bu rapor pahalıya çıkıyor, pazarlık gücünü aşağı çekiyor."
        yeni = [{"text": c}, {"text": c + " Sonuç değişmiyor."}]
        self.assertTrue(_tekrarli_replik_var_mi(eski, yeni))

    def test_callback_tekrar_sayilamaz(self):
        """Aynı rakamı referans alma (LEXICAL UPTAKE) mükerrer sayılmamalı."""
        eski = [{"text": "Bu Lexus ES 300h Türkiye'ye geliyor diyorlar."}]
        yeni = [{"text": "Ama 300h motorun şehir içi tüketimi dizel rakibinin yarısı kadar kalıyor, bu fark uzun vadede bozuluyor."}]
        self.assertFalse(_tekrarli_replik_var_mi(eski, yeni))


class KapanisSorusuTestleri(unittest.TestCase):
    def test_sonuayni_soru_replikteyken_cikarilir(self):
        script = {
            "segments": [
                {"speaker": "female", "tts_tag": "[vurgulu]", "text": "Siz bu fiyata alırdınız mı?"},
            ],
            "yorum_tetikleyici_soru": "Siz bu fiyata alırdınız mı?",
        }
        logs = []
        yeni = _kapanis_sorusunu_ayristir(script, logs.append)
        self.assertEqual(yeni["segments"], [])
        self.assertEqual(len(logs), 1)

    def test_farkli_son_replik_korunur(self):
        script = {
            "segments": [{"speaker": "female", "tts_tag": "", "text": "Sonuç olarak hesap şeffaf değil."}],
            "yorum_tetikleyici_soru": "Siz bu fiyata alırdınız mı?",
        }
        yeni = _kapanis_sorusunu_ayristir(script)
        self.assertEqual(len(yeni["segments"]), 1)

    def test_noktalama_farkli_olsa_dahi_eslestirilir(self):
        script = {
            "segments": [{"speaker": "female", "tts_tag": "", "text": "Siz bu fiyata alırdınız mı."}],
            "yorum_tetikleyici_soru": "Siz bu fiyata alırdınız mı?",
        }
        yeni = _kapanis_sorusunu_ayristir(script)
        self.assertEqual(len(yeni["segments"]), 0)

    def test_bossoru_oldugunda_dokunulmaz(self):
        script = {
            "segments": [{"speaker": "female", "tts_tag": "", "text": "Son cümle burada."}],
            "yorum_tetikleyici_soru": "",
        }
        self.assertIs(_kapanis_sorusunu_ayristir(script), script)


if __name__ == "__main__":
    unittest.main()
