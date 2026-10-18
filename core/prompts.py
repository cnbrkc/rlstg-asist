import os
from datetime import datetime
from zoneinfo import ZoneInfo

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")

def _oku(ad):
    with open(os.path.join(_PROMPT_DIR, ad), encoding="utf-8") as f:
        return f.read()

_TON_PROFILLERI = {
    "eglence": (
        "EĞLENCE AĞIRLIKLI: yaklaşık %25 bilgi, %75 hikâye/reaksiyon/yorum. "
        "En fazla 1-2 güçlü olgusal dayanak seç; teknik veri yığma. Açıyı şaşırtıcı görsel, "
        "gündelik kullanım, doğal mizah veya karakter tepkisi taşısın. Bilgiyi yalnızca hikâyeyi güçlendirdiği yerde kullan."
    ),
    "dengeli": (
        "DENGELİ: yaklaşık %50 bilgi, %50 yorum/reaksiyon/hikâye. Bilgiyi arka arkaya dizme; "
        "aynı teknik özelliği farklı cümlelerle tekrar etme. En fazla 2 bilgi yoğun cümlenin ardından "
        "doğal yorum, kullanım karşılığı, reaksiyon veya geçiş getir."
    ),
    "bilgi": (
        "BİLGİ AĞIRLIKLI: yaklaşık %75 bilgi, %25 yorum/reaksiyon. Birden fazla doğrulanmış olguyu "
        "neden-sonuç ve kullanıcıya etkisiyle açıkla. Teknik ayrıntılar önemli ama katalog gibi sıralanmasın; "
        "her bilgi anlamı veya gerçek kullanım karşılığıyla hikâyeye bağlansın."
    ),
    "teknik": (
        "TEKNİK / DETAYLI: yaklaşık %90 bilgi, %10 yorum. Mekanizma, ölçülebilir veri, donanım farkı, "
        "teknik neden-sonuç ve gerekiyorsa doğrulanmış karşılaştırma önceliklidir. Terimleri doğru ve kısa biçimde açıkla; "
        "liste/katalog dili, veri uydurma ve genel geçer reaksiyonlarla alan doldurma."
    ),
}

def _reels_ton_ayarlarini_hazirla(icerik_tonu):
    ton = (icerik_tonu or "dengeli").strip().lower()
    if ton not in _TON_PROFILLERI:
        ton = "dengeli"
    return ton, _TON_PROFILLERI[ton]

def icerik_tonu_talimati(icerik_tonu, asama="içerik"):
    ton, profil = _reels_ton_ayarlarini_hazirla(icerik_tonu)
    return (
        f"\n\n🚨 RUNTIME İÇERİK TÜRÜ KİLİDİ — {asama.upper()} 🚨\n"
        f"Seçili tür: {ton}. Bu seçim tavsiye değil, bu üretime ait editoryal sözleşmedir.\n"
        f"{profil}\n"
        "Fact Lock, kullanıcı notu ve güvenlik kuralları her zaman üst sınırdır; tür uğruna bilgi uydurma. "
        "Buna karşılık seçili türü varsayılan 'dengeli' tona yuvarlama ve başka bir türün yaklaşımıyla ezme."
    )

# --- Kullanıcı notu (Telegram'da videoyla birlikte gönderilen açıklama) --------
# Kök neden (18.10.2026 üretim şikâyeti): kullanıcı videoyu bir açıklamayla
# gönderdiğinde bu not YALNIZCA Forensic ve Editorial aşamalarına gidiyordu.
# `pipeline._qa_regeneration_loop` production_notes'u parametre olarak alıp
# HİÇBİR yere taşımıyordu; Research, Detective, Hook, Script Writer, Metadata,
# Threads ve Final QA notu hiç görmüyordu. Forensic aşaması aracı başka bir
# modele benzettiğinde üretimin geri kalanı kullanıcının yazdığını değil o yanlış
# kimliği takip ediyordu ("x gönderiyorum, sürekli x videosu hazırlıyor").
# Çözüm: notun etiketi ve öncelik kuralları TEK kaynaktan üretilir; not ham
# metin olarak pipeline'da dolaşır ve her aşamaya aynı biçimde girer.
KULLANICI_NOTU_BASLIGI = "KULLANICI NOTU (içeriğin sahibinin yazdığı açıklama)"
# Worker bir süre notu bu önekle sarıp gönderiyordu; eski kuyruk verisi ve
# workflow_dispatch ile elle tetikleme hâlâ önekli gelebilir.
KULLANICI_NOTU_ESKI_ON_EKLER = (
    "KULLANICI TELEGRAM NOTU (MUTLAK ÖNCELİKLİ):",
    "KULLANICI TELEGRAM NOTU:",
    "ÖNEMLİ VİDEO ANALİZ NOTLARI:",
)
# Telegram video caption sınırı 1024; tavan savunma amaçlıdır (prompt şişmesin).
KULLANICI_NOTU_AZAMI_KARAKTER = 1200


def kullanici_notu_temizle(notes) -> str:
    """Kullanıcı notunu tek kanonik biçime indirger (önek/boşluk temizliği).

    Boş/None girdi '' döner; böylece çağıran taraf `if kullanici_notu:` ile
    notun varlığını test edebilir.
    """
    ham = str(notes or "").strip()
    if not ham:
        return ""
    for on_ek in KULLANICI_NOTU_ESKI_ON_EKLER:
        if ham.casefold().startswith(on_ek.casefold()):
            ham = ham[len(on_ek):].strip()
            break
    ham = " ".join(ham.split())
    return ham[:KULLANICI_NOTU_AZAMI_KARAKTER].strip()


def kullanici_notu_icerik(notes) -> str:
    """Notun content (GİRDİ) bloğu; not yoksa boş dize.

    Not metni prompt'ta BİR KEZ görünmelidir (bkz. tests/test_prompts.py);
    bu yüzden kurallar metni buraya değil `kullanici_notu_kurallari` içine yazılır.
    """
    not_metni = kullanici_notu_temizle(notes)
    return durumu_metne_donustur(KULLANICI_NOTU_BASLIGI, not_metni) if not_metni else ""


def kullanici_notu_kurallari(notes) -> str:
    """Notun prompt tarafındaki ÖNCELİK KURALLARI; not metnini tekrarlamaz.

    Kullanıcının talebi iki yönlü bağlayıcıdır: notta yazan yok sayılamaz,
    ama not TEK kaynak da değildir (video gözlemi + Fact Lock ile birlikte
    kullanılır; doğrulanabilir rakamlarda Fact Lock üstündür).
    """
    if not kullanici_notu_temizle(notes):
        return ""
    return (
        f"\n\n🚨 KULLANICI NOTU KİLİDİ — İÇERİĞİN SAHİBİNDEN GELEN TALEP 🚨\n"
        f"Sana verilen girdide \"{KULLANICI_NOTU_BASLIGI}\" başlığıyla bir metin var. "
        "Bu metni bu içeriğin sahibi yazdı; tavsiye değil üretim sözleşmesidir ve YOK SAYILAMAZ.\n"
        "1) KİMLİK KİLİDİ: Notta geçen marka/model/versiyon adı BİREBİR kullanılır. Başka bir "
        "markaya veya modele benzetme, yakın ada yuvarlama, kısaltma YASAK. Not ile video "
        "görüntüsü çelişse bile nottaki ad esastır ve içerik o konu etrafında kurulur.\n"
        "2) TALEP KİLİDİ: Nottaki somut talepler (vurgulanacak özellik, açı, karşılaştırma, "
        "soru, kaçınılacak konu) üretime yansıtılır; sessizce düşürülmez.\n"
        "3) NOT TEK KAYNAK DEĞİLDİR: Not bir öncelik kilididir; video gözlemi, Fact Lock ve "
        "araştırma verisiyle BİRLİKTE kullanılır. Not kısa veya eksik diye içerik daraltılmaz, "
        "notun kapsamadığı alanlarda normal üretim akışı sürer.\n"
        "4) RAKAM KİLİDİ: Nottaki fiyat/ÖTV/vergi/teknik rakam Fact Lock'ta OBSERVED veya "
        "VERIFIED değilse kesin gerçek gibi söylenmez; ya hiç kullanılmaz ya da açıkça iddia "
        "olarak işaretlenir. Fact Lock ve vergi/ÖTV kilidi bu konuda notun üstündedir.\n"
        "5) Notu kopyala-yapıştır blok olarak koyma; içeriğin doğal diline yedir."
    )


def forensic_analiz_promptunu_olustur(ek_notlar="", sure_saniye=0):
    # ek_notlar zaten pipeline.py tarafından güvenli şekilde ekleniyor. Global değişkene gerek yok.
    template = _oku("forensic_analysis_prompt.txt")
    return template.replace("{ek_notlar_bolumu}", ek_notlar or "").replace("{sure_saniye}", str(sure_saniye))

def research_promptunu_olustur():
    bugun = datetime.now(ZoneInfo("Europe/Istanbul")).date().isoformat()
    guncellik = _oku("guncellik_talimati.txt").replace("{bugunun_tarihi}", bugun)
    return _oku("research_prompt.txt") + "\n" + guncellik

def editorial_promptunu_olustur(icerik_tonu=None):
    return _oku("editorial_prompt.txt") + icerik_tonu_talimati(icerik_tonu, "Editorial Brain")

def caption_promptunu_olustur(icerik_tonu=None):
    return _oku("caption_prompt.txt") + icerik_tonu_talimati(icerik_tonu, "Caption")

# Caption promptunun JSON şema kuyruğu (metadata promptuna farklı şema ile
# yeniden bağlanmak için ayrıştırma noktası).
_CAPTION_SON_KESIM_NOKTASI = "Çıktın SADECE şu Caption şemasına uygun JSON olacak:"

_METADATA_SON = (
    "\n\n⸻\n\n"
    "BAŞLIK (reels_baslik): 30-60 karakter. Ana anahtar kelime + tek güçlü kanca. "
    "Kapak başlığını birebir KOPYALA YASAK; soru işareti ve ünlem sıralaması yok; abartılı süsleme yok.\n\n"
    "reels_aciklama ve reels_hashtag alanlarını yukarıdaki CAPTION BİÇİMİ + HASHTAG KURALLARINA göre üret: "
    "açıklama 700-850 karakter (900'ü ASLA aşma), hashtag TAM 5 adet (daha az ya da fazla YASAK).\n\n"
    "Çıktın SADECE şu Metadata şemasına uygun JSON olacak:\n"
    "{\n"
    "  \"reels_baslik\": \"...\",\n"
    "  \"reels_aciklama\": \"...\",\n"
    "  \"reels_hashtag\": [\"...\", \"...\", \"...\", \"...\", \"...\"]\n"
    "}\n"
)

def metadata_promptunu_olustur(icerik_tonu=None):
    """Metadata (Başlık & Caption) ajanı promptu.

    Kök neden (Eylül 2026): Telegram'a giden caption'ı üreten Metadata ajanının
    promptu 2 satırlıktı — karakter hedefi, 5-hashtag kuralı, Fact Lock sınırları
    hiç yoktu; detaylı caption_prompt.txt yalnız fallback Caption ajanına gidiyordu.
    Caption kuralları tek kaynaktan (caption_prompt.txt) alınır; yalnız JSON şema
    kuyruğu Metadata şemasıyla değiştirilir. Böylece iki ajan arasında kural
    sapması tekrar oluşmaz.
    """
    base = _oku("caption_prompt.txt")
    kesim = base.find(_CAPTION_SON_KESIM_NOKTASI)
    if kesim != -1:
        base = base[:kesim]
    return base + _METADATA_SON + icerik_tonu_talimati(icerik_tonu, "Metadata")

def threads_promptunu_olustur(icerik_tonu=None):
    return _oku("threads_promptu.txt") + icerik_tonu_talimati(icerik_tonu, "Threads")

def qa_promptunu_olustur(icerik_tonu=None):
    return _oku("qa_prompt.txt") + icerik_tonu_talimati(icerik_tonu, "Final QA")

def durumu_metne_donustur(baslik, deger):
    # GEREKSİZ TEKRAR TEMİZLENDİ: Kullanıcı notunu her state'e eklemek token israfıydı.
    return f"### {baslik}\n{deger}"

def girdi_birlestir(*parcalar): 
    return "\n\n".join(str(x) for x in parcalar if x is not None)
