import os
import json
_PROMPT_DIR=os.path.join(os.path.dirname(__file__),"prompts")

# Kullanıcının video analiz notu pipeline boyunca AUTHORITATIVE FACT olarak korunur.
_AKTIF_VIDEO_ANALIZ_NOTU = ""


def _oku(ad):
    with open(os.path.join(_PROMPT_DIR,ad),encoding="utf-8") as f:return f.read()


def _otorite_talimati():
    if not _AKTIF_VIDEO_ANALIZ_NOTU.strip():
        return ""
    return (
        "\n\n🚨 KULLANICI FACT LOCK — MUTLAK ÖNCELİK 🚨\n"
        "Aşağıdaki VİDEO ANALİZ NOTU kullanıcı tarafından doğrudan verilmiştir. "
        "Bu notta yer alan kesin marka/model/variant ve diğer olgusal bilgiler "
        "videodan yaptığın görsel/işitsel çıkarımdan daha yüksek önceliklidir. "
        "Çelişki görürsen kullanıcı bilgisini DOĞRU kabul et; onu değiştirme, "
        "başka modele dönüştürme, UNKNOWN/INFERENCE yapma ve sonraki aşamalara "
        "değiştirilmiş hâlini taşıma. Kullanıcı bilgisi sonraki tüm aşamalarda "
        "aynen korunmalıdır.\n\n"
        "--- KULLANICI VİDEO ANALİZ NOTU ---\n"
        f"{_AKTIF_VIDEO_ANALIZ_NOTU.strip()}\n"
        "--- KULLANICI VİDEO ANALİZ NOTU SONU ---\n"
    )


def forensic_analiz_promptunu_olustur(ek_notlar="",sure_saniye=0):
    global _AKTIF_VIDEO_ANALIZ_NOTU
    _AKTIF_VIDEO_ANALIZ_NOTU = ek_notlar or ""
    template=_oku("forensic_analysis_prompt.txt")
    return template.replace("{ek_notlar_bolumu}", _otorite_talimati()).replace("{sure_saniye}", str(sure_saniye))


def research_promptunu_olustur(): return _oku("research_prompt.txt")+"\n"+_oku("guncellik_talimati.txt")+_otorite_talimati()
def editorial_promptunu_olustur(): return _oku("editorial_prompt.txt")+_otorite_talimati()


def _reels_ton_ayarlarini_hazirla(icerik_tonu):
    ton=(icerik_tonu or "dengeli").strip().lower()
    oranlar={
        "eglence":"EĞLENCE AĞIRLIKLI: yaklaşık %25 bilgi, %75 hikâye/reaksiyon/yorum. Teknik veri yığma. Bilgi yalnızca hikâyeyi taşıdığı yerde kullan.",
        "dengeli":"DENGELİ: yaklaşık %50 bilgi, %50 yorum/reaksiyon/hikâye. Bilgiyi arka arkaya dizme; aynı teknik özelliği farklı cümlelerle tekrar etme. En fazla 2 bilgi yoğun cümleyi art arda kullan, ardından doğal yorum/reaksiyon/geçiş getir.",
        "bilgi":"BİLGİ AĞIRLIKLI: yaklaşık %75 bilgi, %25 yorum/reaksiyon. Teknik ayrıntılar önemli ama katalog gibi sıralanmayacak; her bilgi hikâyeye bağlanacak.",
        "teknik":"TEKNİK ODAKLI: yaklaşık %90 bilgi, %10 yorum. Teknik doğruluk ve açıklık öncelikli; yine de liste/katalog dili kullanılmayacak."
    }
    return ton, oranlar.get(ton, oranlar["dengeli"])


def _reels_kelime_ayarlarini_hazirla(sure_saniye, kelime_hizi_orani=None):
    oran=float(kelime_hizi_orani or 2.4)
    yuvarlama=5
    hedef=max(5, int(round((float(sure_saniye or 30)*oran)/yuvarlama)*yuvarlama))
    minimum=max(5, int(round(hedef*0.90)))
    maksimum=max(minimum, int(round(hedef*1.10)))
    return hedef, minimum, maksimum, oran, yuvarlama


def reels_creative_promptunu_olustur(sure_saniye,icerik_tonu,kelime_hizi_orani=None,ek_talimat=""):
    template=_oku("reels_creative_prompt.txt")
    ton, bilgi_orani = _reels_ton_ayarlarini_hazirla(icerik_tonu)
    hedef, minimum, maksimum, oran, yuvarlama = _reels_kelime_ayarlarini_hazirla(sure_saniye, kelime_hizi_orani)
    replacements={
        "{sure_saniye}":str(sure_saniye),
        "{kelime_sayisi}":str(hedef),
        "{min_kelime}":str(minimum),
        "{max_kelime}":str(maksimum),
        "{kelime_hizi_orani}":str(oran),
        "{kelime_yuvarlama}":str(yuvarlama),
        "{bilgi_orani}":bilgi_orani,
    }
    for eski,yeni in replacements.items(): template=template.replace(eski,yeni)
    runtime=(
        f"\n\nRUNTIME KİLİDİ — BU ÜRETİM İÇİN: Hedef süre {sure_saniye} sn; hedef {hedef} kelime; "
        f"izin verilen aralık {minimum}-{maksimum} kelime; içerik tonu {ton}. "
        "Bu değerler prompt içindeki örneklerden veya önceki üretimlerden bağımsız olarak geçerlidir. "
        "Seslendirme metnini bu kelime aralığının dışına çıkarma. Bilgi yoğunluğunu seçilen tona uygun tut."
    )
    if ek_talimat and ek_talimat.strip(): runtime += "\n\n🚨 YENİDEN ÜRETİM TALİMATI:\n" + ek_talimat.strip()
    return template + runtime + _otorite_talimati()


def caption_promptunu_olustur(): return _oku("caption_prompt.txt")+_otorite_talimati()
def threads_promptunu_olustur(): return _oku("threads_promptu.txt")+_otorite_talimati()
def qa_promptunu_olustur(): return _oku("qa_prompt.txt")+_otorite_talimati()


def durumu_metne_donustur(baslik,deger):
    parca=f"### {baslik}\n{deger}"
    if _AKTIF_VIDEO_ANALIZ_NOTU.strip() and baslik != "KULLANICI VIDEO ANALİZ NOTU":
        parca += (
            "\n\n### KULLANICI VIDEO ANALİZ NOTU — MUTLAK ÖNCELİKLİ KAYNAK\n"
            f"{_AKTIF_VIDEO_ANALIZ_NOTU.strip()}\n"
            "Bu not kullanıcı tarafından verilmiştir. Marka/model/variant ve diğer "
            "kesin kullanıcı bilgileri görsel çıkarımlardan önce gelir. Sonraki "
            "aşamalarda bu bilgiler değiştirilemez, başka modele dönüştürülemez "
            "ve belirsizlik olarak işaretlenemez."
        )
    return parca


def girdi_birlestir(*parcalar): return "\n\n".join(str(x) for x in parcalar if x is not None)
