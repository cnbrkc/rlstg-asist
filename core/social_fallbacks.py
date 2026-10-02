"""Sosyal çıktı (Caption / Threads / Hashtag) için ortak yardımcılar.

Telegram worker, social_entry ve guard katmanları bu dosyadan beslenir; böylece
artifact kontrolü, model kimliği ve güvenli fallback metinleri tek yerde tanımlı kalır.
"""
import re

from core.fiyat_kilidi import turkiye_fiyati_metni

# Yasak dosya/konum desenleri: file system yolları ve media uzantıları sosyal metin olarak gönderilemez.
_ARTIFACT_PREFIXES = ("/tmp/", "/home/runner/", "data/", "./data/", "../")
_ARTIFACT_EXTENSIONS = (".wav", ".mp3", ".m4a", ".aac", ".mp4", ".mov", ".webm")

DEFAULT_HASHTAGS = ["otoxtra", "otomobil", "araba", "otomobilhaber", "arabasever"]


def text(value) -> str:
    """None / boş / sayısal değerleri güvenli string'e çevirir."""
    return str(value or "").strip()


def looks_like_artifact(value) -> bool:
    """Yerel dosya yolu veya medya uzantısı içeren değeri sosyal metin olarak reddeder."""
    s = text(value)
    if not s:
        return True
    lower = s.lower()
    if lower.startswith(_ARTIFACT_PREFIXES):
        return True
    if re.search(r"\.(wav|mp3|m4a|aac|mp4|mov|webm)(?:\b|$)", lower):
        return True
    if "\\tmp\\" in lower or "\\home\\runner\\" in lower:
        return True
    return False


def sanitize_hashtags(raw) -> list:
    """Hashtag listesini temizler: # karakterini kaldırır, geçersiz chars'ı siler."""
    return [
        re.sub(r"[^\wÇĞİÖŞÜçğıöşü-]", "", text(x).lstrip("#"))
        for x in (raw or [])
        if text(x)
    ]


def first_fact(fact_state) -> str:
    """Fact Lock içinden ilk OBSERVED/VERIFIED olguyu döndürür."""
    facts = (fact_state or {}).get("facts") if isinstance(fact_state, dict) else []
    if not isinstance(facts, list):
        return ""
    for item in facts:
        if isinstance(item, dict) and text(item.get("fact")):
            status = text(item.get("status")).upper()
            if status in {"OBSERVED", "VERIFIED"}:
                return text(item.get("fact"))
    return ""


def model_identity(video_state) -> str:
    """Video state içinden marka/model kimliğini çıkarır; yoksa 'bu araç' döner."""
    if not isinstance(video_state, dict):
        return ""
    ident = video_state.get("video_identity") or {}
    if not isinstance(ident, dict):
        return ""
    brand = text(ident.get("brand"))
    model = text(ident.get("exact_model"))
    if model and model.upper() != "UNKNOWN":
        return model
    if brand and brand.upper() != "UNKNOWN":
        return brand
    return ""


def caption_fallback(fact_state, editorial_state, video_state) -> tuple:
    """Caption modeli boş/artifact dönerse güvenli Fact Lock tabanlı caption üretir."""
    identity = model_identity(video_state) or "bu araç"
    editorial = editorial_state if isinstance(editorial_state, dict) else {}
    core = text(editorial.get("core_story"))
    why = text(editorial.get("why_it_matters"))
    fact = first_fact(fact_state)
    # Doğrulanmış Türkiye fiyatı varsa fallback caption da onu taşır: kullanıcı
    # "açıklamada fiyatları verelim" notuyla üretim istediğinde fiyat bilgisi
    # yalnızca model caption'ında değil, güvenli fallback'te de görünmeli.
    fiyat = turkiye_fiyati_metni(fact_state)
    parts = [
        f"{identity}: videonun ötesinde asıl merak edilen taraf biraz da burada başlıyor.",
    ]
    if fiyat:
        parts.append(f"Türkiye fiyatı: {fiyat}.")
    parts.extend([
        core or fact or "Videoda öne çıkan detayları Fact Lock sınırları içinde takip ediyoruz.",
        why or fact,
        "Rakamlar ve görünen detaylar bir yana, otomobilde asıl mesele bunların gerçek kullanımda ne ifade ettiği.",
        "Siz olsanız bu noktada hangi detaya daha çok önem verirdiniz?",
    ])
    return "\n\n".join(p for p in parts if p)[:900].rstrip(), list(DEFAULT_HASHTAGS)


def limit_threads_text(value, max_characters=500) -> str:
    """Threads metnini karakter sınırına, mümkünse cümle/kelime sonunda indir."""
    value = str(value or "").strip()
    max_characters = max(1, int(max_characters))
    if len(value) <= max_characters:
        return value

    prefix = value[:max_characters]
    sentence_ends = [
        match.end()
        for match in re.finditer(r"[.!?](?=[\"’”']?(?:\s|$))", prefix)
    ]
    if sentence_ends and sentence_ends[-1] >= max_characters * 0.6:
        return prefix[:sentence_ends[-1]].rstrip()

    boundary = prefix.rfind(" ")
    if boundary > 0:
        prefix = prefix[:boundary]
    return prefix.rstrip(" ,;:-")


def threads_fallback(fact_state, editorial_state, video_state) -> str:
    """Threads modeli boş/artifact dönerse güvenli Fact Lock tabanlı metin üretir."""
    identity = model_identity(video_state) or "Bu araç"
    editorial = editorial_state if isinstance(editorial_state, dict) else {}
    discussion = text(editorial.get("discussion_territory"))
    core = text(editorial.get("core_story"))
    fact = first_fact(fact_state)
    fiyat = turkiye_fiyati_metni(fact_state)
    govde = []
    if fiyat:
        govde.append(f"Türkiye fiyatı {fiyat}")
    govde.append(discussion or core or fact or "Bu içerikte asıl mesele, videoda görünen detayın gerçek kullanımda ne ifade ettiği.")
    return limit_threads_text(f"{identity} tarafında bence tartışma tam burada başlıyor: " + "; ".join(govde))
