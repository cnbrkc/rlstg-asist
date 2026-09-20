"""Anlatım modu (tek ses / çift ses) karar katmanı.

Editorial Brain'den SONRA, Reels Creative'den ÖNCE çalışır ve tek bir iş yapar:
bu içerik için SOLO_FEMALE, SOLO_MALE veya DUO seçmek.

Karar editorial_state içine 'anlatim_modu_karari' olarak yazılır. Reels Creative,
Duo script ve TTS bu karara uyar. Karar alınamazsa boş sözlük döner; bu durumda
pipeline eskisi gibi davranır (Reels Creative'in kendi seçimi kullanılır).

Acil durum düğmesi: ANLATIM_MODU_ZORLA ortam değişkeni DUO / SOLO_FEMALE /
SOLO_MALE olarak ayarlanırsa AI sorgusu atlanır ve o mod kullanılır.
"""
import json
import os

from core.prompts import durumu_metne_donustur, girdi_birlestir

GECERLI_MODLAR = ("SOLO_FEMALE", "SOLO_MALE", "DUO")

ANLATIM_MODU_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "anlatim_modu": {
            "type": "STRING",
            "enum": ["SOLO_FEMALE", "SOLO_MALE", "DUO"],
            "description": "SOLO_FEMALE / SOLO_MALE / DUO. Varsayılan yoktur; yalnızca bu içeriğin ihtiyacına göre seç.",
        },
        "duo_katma_degeri": {
            "type": "STRING",
            "description": "İkinci sesin bu videoya katacağı SOMUT değer (ne söyler, neye karşı çıkar, hangi tepkiyi verir). Somut değer söyleyemiyorsan 'yok' yaz.",
        },
        "solo_katma_degeri": {
            "type": "STRING",
            "description": "Tek sesin bu içerikte sağlayacağı somut avantaj (tempo, netlik, akıcılık vb.).",
        },
        "guven": {"type": "NUMBER", "description": "0-1 arası karar güveni."},
        "gerekce": {
            "type": "STRING",
            "description": "Kararın 1-2 cümlelik Türkçe gerekçesi (izleyici tutma, ilk 3 saniye, tempo, tartışma potansiyeli açısından).",
        },
    },
    "required": ["anlatim_modu", "duo_katma_degeri", "solo_katma_degeri", "guven", "gerekce"],
}

# ---------------------------------------------------------------------------
# PROMPT — buradaki metni istediğin gibi değiştirebilirsin.
# {sure_saniye} ve {ton} çalışma anında otomatik doldurulur.
# ---------------------------------------------------------------------------
ANLATIM_MODU_PROMPT = """Sen otoXtra için çalışan kısa video (Instagram Reels / Threads / Facebook) anlatım stratejistisin.

GÖREV: Aşağıdaki içerik için seslendirmenin TEK SES mi yoksa ÇİFT SES mi olması gerektiğine karar ver.
Tek işin mod seçmek ve gerekçelendirmek. Metin yazma, gerçekleri değiştirme, yeni bilgi uydurma.

SEÇENEKLER:
- SOLO_FEMALE: Tek anlatıcı, kadın sesi (Autonoe). Doğal, zeki, merak uyandıran, akıcı anlatım.
- SOLO_MALE: Tek anlatıcı, erkek sesi (Charon). Otomobil meraklısı, sakin, net, güven veren anlatım.
- DUO: İki karakter (Autonoe + Charon), kısa ve doğal bir sohbet.

BAĞLAM:
- Video süresi: yaklaşık {sure_saniye} saniye
- İçerik türü: {ton}

KARARI NASIL VERECEKSİN:
Ölçüt izleyicinin ilk 3 saniyede kalması, videoyu sonuna kadar izlemesi, tekrar izlemesi, yorum yapması ve paylaşmasıdır.
Sosyal medya platformlarının tek ya da çift sesi doğrudan ödüllendirdiğine dair kesin bir kural yoktur; bu yüzden algoritma hakkında kesin iddia kurma, kararı izleyiciyi tutma mantığıyla gerekçelendir.

DUO uygun olur, eğer:
- içerikte gerçek bir zıtlık, soru-cevap veya doğal bir tartışma potansiyeli varsa,
- şaşırtıcı bir bilgiye verilen tepki videoyu güçlendiriyorsa,
- iki bakış açısı (ör. heyecan ve şüphe, tasarım ve mühendislik) içerikte zaten mevcutsa,
- video yeterince uzunsa (kabaca 20 saniye ve üstü) ve söz sırası değişimi izleyiciyi yormuyorsa.

TEK SES uygun olur, eğer:
- tek net bir hikâye, bilgi veya duygu akıcı biçimde anlatılacaksa,
- içerik teknik veya sayısal bilgi yoğunsa ve bölünmeden anlatılması daha anlaşılırsa,
- görüntü kendini anlatıyorsa ve ses yalnızca yönlendiriyorsa,
- video kısaysa (kabaca 15 saniye ve altı) ve söz sırası değişimi süre yiyorsa,
- diyalog zorlama ya da yapay skeç gibi duracaksa.

TEK SES seçtiysen kadın mı erkek mi: içeriğin tonuna göre seç. Mühendislik, performans ve sakin otorite ağırlıklıysa SOLO_MALE; merak, tasarım, yaşam tarzı, sürpriz ve hikâye ağırlıklıysa SOLO_FEMALE. Bu kesin kural değil, ton rehberidir.

ÖNEMLİ:
- Varsayılan mod YOKTUR. Üç seçenek de eşit derecede geçerlidir.
- Sırf iki ses "daha canlı" görünüyor diye DUO seçme. DUO'yu yalnızca ikinci sesin bu içeriğe somut katkısını (ne söyleyecek, neye karşı çıkacak, hangi tepkiyi verecek) adlandırabiliyorsan seç. Adlandıramıyorsan tek ses seç.
- Sırf güvenli diye tek ses de seçme; içerik gerçekten iki sesle daha iyi tutuyorsa DUO seç.
- Kullanıcı notu ve Fact Lock içindeki bilgiler değişmez; karar yalnızca sunum biçimine dairdir.

ÇIKTI: Yalnızca şemaya uygun JSON."""


def anlatim_modu_promptunu_olustur(sure_saniye, ton):
    try:
        sure_metni = f"{float(sure_saniye):.0f}"
    except (TypeError, ValueError):
        sure_metni = "bilinmiyor"
    ton_metni = str(ton or "dengeli").strip().lower() or "dengeli"
    return ANLATIM_MODU_PROMPT.replace("{sure_saniye}", sure_metni).replace("{ton}", ton_metni)


def _sozluk(deger):
    if isinstance(deger, dict):
        return deger
    if isinstance(deger, str):
        try:
            parsed = json.loads(deger.strip())
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _mod_temizle(deger):
    metin = str(deger or "").strip().upper().replace("-", "_").replace(" ", "_")
    return metin if metin in GECERLI_MODLAR else ""


def _guven(deger):
    try:
        return max(0.0, min(1.0, float(deger)))
    except (TypeError, ValueError):
        return 0.0


def anlatim_modu_karar_ver(router, video_state, fact_state, editorial_state, sure_saniye, ton, log):
    """Ayrı ve basit bir AI sorgusuyla tek/çift ses kararını verir.

    Dönüş: {"mode", "reason", "confidence", "duo_value", "solo_value", "source"}
    Karar alınamazsa boş sözlük {} döner (pipeline eski davranışına düşer).
    """
    zorla = _mod_temizle(os.environ.get("ANLATIM_MODU_ZORLA", ""))
    if zorla:
        log(f"🎚️ Anlatım modu ortam değişkeniyle zorlandı: {zorla}")
        return {
            "mode": zorla,
            "reason": "ANLATIM_MODU_ZORLA ortam değişkeni.",
            "confidence": 1.0,
            "duo_value": "",
            "solo_value": "",
            "source": "env",
        }

    try:
        editorial_gorunum = editorial_state
        if isinstance(editorial_state, dict):
            editorial_gorunum = {k: v for k, v in editorial_state.items() if k != "anlatim_modu_karari"}
        content = girdi_birlestir(
            durumu_metne_donustur("VIDEO STATE", video_state),
            durumu_metne_donustur("FACT LOCK", fact_state),
            durumu_metne_donustur("EDITORIAL", editorial_gorunum),
        )
        sonuc, _model = router.metin_uret(
            content,
            anlatim_modu_promptunu_olustur(sure_saniye, ton),
            ANLATIM_MODU_SCHEMA,
            log,
            arama_kullan=False,
        )
        veri = _sozluk(sonuc)
        mod = _mod_temizle(veri.get("anlatim_modu"))
        if not mod:
            raise ValueError(f"geçersiz anlatım modu: {str(veri.get('anlatim_modu'))[:40]}")
        karar = {
            "mode": mod,
            "reason": str(veri.get("gerekce") or "").strip(),
            "confidence": _guven(veri.get("guven")),
            "duo_value": str(veri.get("duo_katma_degeri") or "").strip(),
            "solo_value": str(veri.get("solo_katma_degeri") or "").strip(),
            "source": "ai",
        }
        log(f"🎚️ Anlatım modu kararı: {mod} (güven {karar['confidence']:.2f}) | {karar['reason'][:160]}")
        return karar
    except Exception as exc:
        log(f"⚠️ Anlatım modu kararı alınamadı; Reels Creative'in kendi seçimi kullanılacak: {str(exc)[:160]}")
        return {}


def _konusmaci(mod):
    return "female" if mod == "SOLO_FEMALE" else "male"


def mod_kilit_talimati(karar):
    """Reels Creative'e giden 'bu mod kesinleşti' talimatı. Karar yoksa boş metin."""
    mod = _mod_temizle((karar or {}).get("mode"))
    if not mod:
        return ""
    if mod == "DUO":
        return (
            "ANLATIM MODU KİLİDİ (bu üretim için kesin karar): DUO. "
            "anlatim_modu ve duo_stratejisi.uygunluk alanlarına DUO yaz. "
            "Konuşma haritasında female ve male birlikte, doğal karşılıklı konuşsun. "
        )
    ses = _konusmaci(mod)
    return (
        f"ANLATIM MODU KİLİDİ (bu üretim için kesin karar): {mod}. "
        f"anlatim_modu ve duo_stratejisi.uygunluk alanlarına {mod} yaz. "
        f"Tek anlatıcı vardır ve yalnızca {ses} konuşur; diyalog, soru-cevap veya ikinci karaktere hitap kurma. "
        f"seslendirme_metni tek kişinin akıcı anlatımı olsun. konusma_haritasi içindeki tüm segmentlerin speaker değeri "
        f"{ses} olsun; hook_speaker ve ending_speaker da {ses} olsun. "
    )


def mod_kilidini_uygula(reels_state, karar):
    """Modelin Reels Creative çıktısını karara uydurur (model sapsa bile mod sabit kalır)."""
    mod = _mod_temizle((karar or {}).get("mode"))
    if not mod or not isinstance(reels_state, dict):
        return reels_state

    reels = dict(reels_state)
    reels["anlatim_modu"] = mod

    strateji = reels.get("duo_stratejisi")
    strateji = dict(strateji) if isinstance(strateji, dict) else {}
    strateji["uygunluk"] = mod

    if mod != "DUO":
        ses = _konusmaci(mod)
        strateji["hook_speaker"] = ses
        strateji["ending_speaker"] = ses
        strateji["female_agirligi"] = 1.0 if ses == "female" else 0.0
        strateji["male_agirligi"] = 1.0 if ses == "male" else 0.0
        strateji["interaction_level"] = 0.0
        strateji["tension_level"] = 0.0

        harita = reels.get("konusma_haritasi")
        if isinstance(harita, list):
            yeni = []
            for item in harita:
                if isinstance(item, dict):
                    kopya = dict(item)
                    kopya["speaker"] = ses
                    yeni.append(kopya)
            reels["konusma_haritasi"] = yeni

    reels["duo_stratejisi"] = strateji
    return reels
