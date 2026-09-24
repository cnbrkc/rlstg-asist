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

def threads_promptunu_olustur(icerik_tonu=None):
    return _oku("threads_promptu.txt") + icerik_tonu_talimati(icerik_tonu, "Threads")

def qa_promptunu_olustur(icerik_tonu=None):
    return _oku("qa_prompt.txt") + icerik_tonu_talimati(icerik_tonu, "Final QA")

def durumu_metne_donustur(baslik, deger):
    # GEREKSİZ TEKRAR TEMİZLENDİ: Kullanıcı notunu her state'e eklemek token israfıydı.
    return f"### {baslik}\n{deger}"

def girdi_birlestir(*parcalar): 
    return "\n\n".join(str(x) for x in parcalar if x is not None)
