"""Doğrulanmış Türkiye fiyatını içeriğe taşıyan talimat katmanı.

Kök neden (01.10.2026 üretim logu): kullanıcı notu "Kia Seltos Türkiye'de
satışa çıktı. Açıklamada fiyatları verelim, duyuru videosu olacak" idi. Web
aramaları 'UNKNOWN' yer tutucusu yüzünden sonuçsuz kalınca Fact Lock'ta fiyat
alanı boş kaldı; ÖTV kilidi matrahı bulamayınca ARALIK'a düştü ve seslendirme
ile açıklama kelime bütçesini "matrah dilimine göre %75 veya %80 veya %90 veya
%100" bracket'larına harcadı — fiyat hiç söylenmedi.

Bu modül: Fact Lock'ta somut, TL cinsinden doğrulanmış fiyat varsa onu
seslendirme/kapak/caption metinlerine ZORUNLU kılar; fiyat yoksa sessiz kalır.
Vergi/ÖTV oranı işine karışmaz (o iş core.otv_kilidi'dedir).
"""
import re

# "1.325.000 TL", "1.325.000TL", "850 bin TL", "1.3 milyon TL", "1325000 TL",
# "1.325.000 ₺", "950.000 lira"
_TL_TUTAR = re.compile(
    r"(\d[\d.,]*)\s*(bin|bın|milyon|milyar)?\s*(?:TL|₺|lira)(?![A-Za-zÇĞİÖŞÜçğıöşü])",
    re.I,
)
# Fiyat alanının "fiyat yok" gibi olumsuz metin olduğunu ayırt etmek için
# kullanılan makul fiyat bandı (TL).
_TL_ALT_SINIR = 50_000
_TL_UST_SINIR = 100_000_000
_CARPANLAR = {"bin": 1_000, "bın": 1_000, "milyon": 1_000_000, "milyar": 1_000_000_000}

# Fiyat metninin YURT DIŞI fiyatı / kur çevirisi olduğunu ayırt etmek için
# kalıp. Research promptu "çıplak kur çevirisini Türkiye satış fiyatı gibi
# sunma" diyor; bu metinler Türkiye fiyatı sayılmaz. Kalıp TUTARA bağlıdır:
# "Euro NCAP 5 yıldız, fiyat 1.325.000 TL" gibi meşru Türkiye fiyatı cümlesi
# "euro" geçtiği için elenmez; "25.000 EUR" / "yurt dışında ... TL" elenir.
_YURT_DISI_KALIP = re.compile(
    r"(?:\d[\d.,]*\s*(?:eur|usd|gbp|dolar|euro|avro)\b)"
    r"|(?:[$€£]\s*\d)"
    r"|(?:yurt\s*d[ıi][şs]\w*)"
    r"|(?:d[ıi][şs]\s*(?:pazar|piyasa))",
    re.I,
)


def _yurt_disi_fiyati_mi(metin) -> bool:
    return bool(_YURT_DISI_KALIP.search(str(metin or "")))


def tl_tutari_coz(rakam: str, birim: str = "") -> int | None:
    """Türkçe yazım tutarını tam sayı TL'ye çözer ('1.325.000' → 1325000)."""
    s = str(rakam or "").strip().rstrip(".,")
    if not s:
        return None
    if "," in s and "." in s:
        # 1.325.000,50 → binlik nokta, ondalık virgül
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        parcalar = s.split(",")
        # 1,325,000 → binlik virgül ; 1,3 → ondalık
        s = s.replace(",", "") if len(parcalar) > 1 and len(parcalar[-1]) == 3 else s.replace(",", ".")
    elif s.count(".") > 1:
        # 1.325.000 → hepsi binlik ayırıcı
        s = s.replace(".", "")
    try:
        deger = float(s)
    except ValueError:
        return None
    carpan = _CARPANLAR.get(str(birim or "").strip().casefold(), 1)
    return int(round(deger * carpan))


def metindeki_tl_tutarlar(metin) -> list:
    """Metindeki makul fiyat bandındaki TL tutarlarını döner."""
    tutarlar = []
    for m in _TL_TUTAR.finditer(str(metin or "")):
        deger = tl_tutari_coz(m.group(1), m.group(2) or "")
        if deger is not None and _TL_ALT_SINIR <= deger <= _TL_UST_SINIR:
            tutarlar.append(deger)
    return tutarlar


def turkiye_fiyati_metni(fact_state) -> str:
    """Fact Lock'ta doğrulanmış, TL cinsinden somut fiyat metnini döner.

    Yalnızca `turkiye_fiyati` ve OBSERVED/VERIFIED fact'ler taranır; yurt dışı
    fiyatı / kur karşılığı (USD, EUR) asla Türkiye fiyatı olarak döndürülmez.
    """
    if not isinstance(fact_state, dict):
        return ""
    alan = str(fact_state.get("turkiye_fiyati") or "").strip()
    if alan and metindeki_tl_tutarlar(alan) and not _yurt_disi_fiyati_mi(alan):
        return alan
    for fact in fact_state.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        if str(fact.get("status") or "").strip().upper() not in ("VERIFIED", "OBSERVED"):
            continue
        metin = str(fact.get("fact") or "").strip()
        if metin and metindeki_tl_tutarlar(metin) and not _yurt_disi_fiyati_mi(metin):
            return metin
    return ""


def fiyat_talimati(fact_state) -> str:
    """Doğrulanmış fiyat varsa içerik üretimine zorunlu fiyat talimatı üretir."""
    fiyat = turkiye_fiyati_metni(fact_state)
    if not fiyat:
        return ""
    return (
        "\n\n🚨 FİYAT KİLİDİ — BU ÜRETİM İÇİN ZORUNLU: Fact Lock'ta doğrulanmış "
        f"Türkiye fiyatı var: “{fiyat}”.\n"
        "- Seslendirme ve kapak başlığı bu fiyatı SOMUT rakamla vermeli; "
        "fiyatı 'pahalı', 'uygun', 'fiyat-performans' gibi boş sıfatla geçiştirme.\n"
        "- Caption/açıklama fiyatı ve varsa donanım/teknik karşılığını net cümlelerle "
        "yazmalı; seslendirmedeki fiyatı birebir tekrar etme, ekstra fiyat/donanım detayı ekle.\n"
        "- Yalnızca Fact Lock'taki rakamı kullan; başka fiyat, indirim, kampanya veya "
        "'şu fiyata çıkar' tahmini UYDURMA.\n"
    )
