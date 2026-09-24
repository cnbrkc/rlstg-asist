"""Anlatım modu (tek ses / çift ses) karar katmanı."""
import json
import os

from core.prompts import durumu_metne_donustur, girdi_birlestir

GECERLI_MODLAR = ("SOLO_FEMALE", "SOLO_MALE", "DUO")

# DÜZELTİLDİ: JSON standardına uygun küçük harfli tip tanımları.
ANLATIM_MODU_SCHEMA = {
    "type": "object",
    "properties": {
        "anlatim_modu": {
            "type": "string",
            "enum": ["SOLO_FEMALE", "SOLO_MALE", "DUO"],
            "description": "SOLO_FEMALE / SOLO_MALE / DUO. Yalnızca bu içeriğin ihtiyacına göre seç.",
        },
        "duo_katma_degeri": {
            "type": "string",
            "description": "İkinci sesin bu videoya katacağı SOMUT değer. Somut değer yoksa 'yok' yaz.",
        },
        "solo_katma_degeri": {
            "type": "string",
            "description": "Tek sesin bu içerikte sağlayacağı somut avantaj.",
        },
        "guven": {"type": "number", "description": "0-1 arası karar güveni."},
        "gerekce": {
            "type": "string",
            "description": "Kararın 1-2 cümlelik Türkçe gerekçesi.",
        },
    },
    "required": ["anlatim_modu", "duo_katma_degeri", "solo_katma_degeri", "guven", "gerekce"],
}

# TEMİZLENDİ: Gereksiz uyarılar (metin yazma, algoritma vb.) çıkarıldı. Sadece karar mantığı bırakıldı.
ANLATIM_MODU_PROMPT = """Sen otoXtra için kısa video anlatım stratejistisin.
GÖREV: Aşağıdaki içerik için seslendirmenin TEK SES mi yoksa ÇİFT SES mi olması gerektiğine karar ver.
SEÇENEKLER:
- SOLO_FEMALE: Tek anlatıcı, kadın sesi (Autonoe). Doğal, zeki, merak uyandıran.
- SOLO_MALE: Tek anlatıcı, erkek sesi (Charon). Otomobil meraklısı, sakin, net.
- DUO: İki karakter (Autonoe + Charon), kısa ve doğal bir sohbet.

BAĞLAM:
- Video süresi: yaklaşık {sure_saniye} saniye
- İçerik türü: {ton}

KARARI NASIL VERECEKSİN:
Ölçüt izleyicinin ilk 3 saniyede kalması, videoyu sonuna kadar izlemesi, tekrar izlemesi, yorum yapması ve paylaşmasıdır.

DUO uygun olur, eğer:
- Video 30 saniye ve üstüyse (öncelikli tercih),
- İçerikte şaşırtıcı bir bilgi, ilginç bir detay veya tartışma potansiyeli varsa,
- Bir bilgiye verilen tepki, karşı görüş veya merak sorusu videoyu güçlendiriyorsa,
- İzleyicinin "ben de böyle düşünüyorum" veya "katılmıyorum" diyeceği bir açı varsa.
NOT: Otomobil videoları genelde iki kişinin sohbetiyle daha doğal ve izlenesi olur. İkinci sesin "ne söyleyeceğini" bulmak için çok özel bir zıtlık aramana gerek yok; merak, şaşkınlık veya pratik kullanım sorusu bile DUO için yeterli gerekçedir.

TEK SES uygun olur, eğer:
- Video 15 saniye ve altındaysa,
- İçerik çok yoğun teknik veri içeriyorsa ve bölününce anlaşılmaz hale gelecekse,
- Görüntü tamamen kendini anlatıyorsa ve ses sadece arka plan yönlendirmesiyse,
- İçerik tek bir duygusal hikâye anlatıyorsa ve ikinci ses bu duyguyu bozacaksa.

ÖNEMLİ:
- Karar verirken DUO'yu öncelikli düşün. Tek ses ancak yukarıdaki TEK SES koşullarından biri NET olarak karşılanıyorsa seçilsin.
- "Teknik bilgi var" tek başına tek ses gerekçesi DEĞİLDİR. Teknik bilgi iki kişinin sohbetiyle çok daha anlaşılır olur.
- TEK SES seçtiysen kadın mı erkek mi: VARSAYILAN SEÇİM SOLO_FEMALE'dir. Yalnızca içerik çok ağır mühendislik odaklıysa ve kadın sesi kesinlikle uyumsuz olacaksa SOLO_MALE seç. Otomobil içeriği olması tek başına SOLO_MALE gerekçesi değildir.
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
        log(f"⚠️ Anlatım modu kararı alınamadı; güvenli varsayılan mod (DUO) kullanılacak: {str(exc)[:160]}")
        return {}
