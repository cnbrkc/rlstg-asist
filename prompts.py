import os
import json
_PROMPT_DIR=os.path.join(os.path.dirname(__file__),"prompts")

# Kullanıcının video analiz notu pipeline boyunca AUTHORITATIVE FACT olarak korunur.
# İlk forensic çağrısından sonra notun kaybolmasını veya sonraki modellerin
# görsel çıkarımıyla bu bilgiyi sessizce değiştirmesini engeller.
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
def reels_creative_promptunu_olustur(sure_saniye,icerik_tonu,kelime_hizi_orani=None): return _oku("reels_creative_prompt.txt")+f"\nHedef süre: {sure_saniye} saniye. Ton: {icerik_tonu}. Kelime oranı: {kelime_hizi_orani or 2.4}."+_otorite_talimati()
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
