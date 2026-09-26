"""Türkiye binek ÖTV oranını araca kilitler ve tüm çıktılarda aynı rakamı zorlar.

Son üretim hatası: hibrit bir araçta seslendirme %70, açıklama %170 dedi;
doğru dilim %220 idi. Üçü de aynı tablonun FARKLI satırları. Model, motor
hacmi / elektrik motoru kW / HEV-PHEV ayrımı olmadan satır seçiyor; caption
promptu da seslendirmeyi tekrar etmemeyi 'başka rakam yaz' diye yorumluyor.

Bu modül oranı uydurmaz. Şartlar tek dilime iniyorsa kilitler, inmiyorsa
tek yüzde yazmayı yasaklar. Kilit hem Fact Lock metnine hem seslendirme /
açıklama / Threads / kapağa aynı geçişle uygulanır.
"""
import re

# 24.07.2025 tarihli 10115 sayılı Cumhurbaşkanı Kararı ile güncellenen
# 4760 sayılı Kanun (II) sayılı liste, 87.03. 2026 GİB listesi ve sektör
# tabloları aynı dilimleri taşıyor. Matrah eşikleri değişirse burası güncellenir.
OTV_TABLO_KAYNAK = (
    "4760 sayılı ÖTV Kanunu (II) sayılı liste, 87.03; "
    "24.07.2025 tarihli 10115 sayılı Cumhurbaşkanı Kararı"
)
OTV_TABLO_TARIH = "2025-07-24"

_FOLD = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")
_BASKA_MARKALAR = (
    "bmw", "mercedes", "audi", "volkswagen", "toyota", "honda", "hyundai", "kia",
    "volvo", "peugeot", "renault", "ford", "tesla", "byd", "togg", "lexus",
    "porsche", "skoda", "seat", "fiat", "opel", "nissan", "mazda", "cupra",
    "mg", "chery", "geely", "citroen", "dacia", "jeep", "mini", "suzuki",
)
_ONLAR = {
    "on": 10, "yirmi": 20, "otuz": 30, "kirk": 40, "elli": 50,
    "altmis": 60, "yetmis": 70, "seksen": 80, "doksan": 90,
}
_BIRLER = {
    "bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5,
    "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9,
}
_ONLAR_YAZ = (
    (90, "doksan"), (80, "seksen"), (70, "yetmiş"), (60, "altmış"), (50, "elli"),
    (40, "kırk"), (30, "otuz"), (20, "yirmi"), (10, "on"),
)
_BIRLER_YAZ = (
    (9, "dokuz"), (8, "sekiz"), (7, "yedi"), (6, "altı"), (5, "beş"),
    (4, "dört"), (3, "üç"), (2, "iki"), (1, "bir"),
)
_ESIK_CC = {1400, 1600, 1800, 2000, 2500}
_YUZDE_SON_EK = r"(?:['’](?:lik|lık|luk|lük|i|ı|u|ü|in|ın|un|ün|e|a|de|da|den|dan))?"


def _fold(metin) -> str:
    return str(metin or "").casefold().translate(_FOLD)


def sayi_yazi(n: int) -> str:
    """220 → 'iki yüz yirmi', 170 → 'yüz yetmiş', 70 → 'yetmiş'."""
    n = int(n)
    if n <= 0:
        return str(n)
    parca = []
    if n >= 100:
        yuz = n // 100
        parca.append("yüz" if yuz == 1 else f"{dict(_BIRLER_YAZ).get(yuz, str(yuz))} yüz")
        n %= 100
    if n >= 10:
        for deger, ad in _ONLAR_YAZ:
            if n >= deger:
                parca.append(ad)
                n -= deger
                break
    if n:
        parca.append(dict(_BIRLER_YAZ).get(n, str(n)))
    return " ".join(parca)


def _yuzde_kelime_oku(tokenlar) -> tuple:
    if not tokenlar:
        return None, 0
    i = 0
    toplam = 0
    if tokenlar[0] == "yuz":
        toplam = 100
        i = 1
    elif len(tokenlar) > 1 and tokenlar[1] == "yuz" and tokenlar[0] in _BIRLER:
        toplam = _BIRLER[tokenlar[0]] * 100
        i = 2
    else:
        toplam = 0
    if i == 0 and tokenlar[0] not in _ONLAR and tokenlar[0] not in _BIRLER:
        return None, 0
    if i < len(tokenlar) and tokenlar[i] in _ONLAR:
        toplam += _ONLAR[tokenlar[i]]
        i += 1
    if i < len(tokenlar) and tokenlar[i] in _BIRLER:
        toplam += _BIRLER[tokenlar[i]]
        i += 1
    if i == 0 or not (25 <= toplam <= 300):
        return None, 0
    return toplam, i


class _Eslesme:
    def __init__(self, oran, bas, son, bicim):
        self.oran = int(oran)
        self.bas = bas
        self.son = son
        self.bicim = bicim  # yuzde_kelime | yuzde_rakam | yuzde_isareti_on | yuzde_isareti_son


def _otv_baglami_mi(pencere: str) -> bool:
    w = _fold(pencere)
    otv = "otv" in w or "ozel tuketim" in w
    mtv_kdv = bool(re.search(r"\bmtv\b|\bkdv\b|tasitlar vergisi|motorlu tasit", w))
    if otv:
        return True
    if mtv_kdv:
        return False
    return "vergi" in w


def _esik_mi(pencere: str) -> bool:
    return bool(re.search(
        r"geçmeyen|gecmeyen|geçenler|gecenler|aşmayan|asmayan|üzeri|uzeri|"
        r"altı\b|alti\b|altında|altinda|arası|arasi|dilim|kadar",
        _fold(pencere),
    ))


def otv_eslesmeleri(metin: str):
    """Metindeki ÖTV/vergi yüzdelerini, konuşma bağlamındakileri döndürür."""
    ham = str(metin or "")
    if not ham:
        return []
    bulunan = []

    rakam = re.compile(
        r"%\s*(\d{1,3})" + _YUZDE_SON_EK +
        r"|(\d{1,3})\s*%" + _YUZDE_SON_EK +
        r"|y[üu]zde\s+(\d{1,3})" + _YUZDE_SON_EK,
        re.I,
    )
    for m in rakam.finditer(ham):
        oran = next(int(g) for g in m.groups() if g)
        if not 25 <= oran <= 300:
            continue
        pencere = ham[max(0, m.start() - 80):m.end() + 40]
        if not _otv_baglami_mi(pencere):
            continue
        if m.group(1):
            bicim = "yuzde_isareti_on"
        elif m.group(2):
            bicim = "yuzde_isareti_son"
        else:
            bicim = "yuzde_rakam"
        bulunan.append(_Eslesme(oran, m.start(), m.end(), bicim))

    for m in re.finditer(r"y[üu]zde", ham, re.I):
        kalan = ham[m.end():]
        bosluk = re.match(r"\s+", kalan)
        kayma = bosluk.end() if bosluk else 0
        parca = kalan[kayma:]
        kelimeler = list(re.finditer(r"[A-Za-zÇĞİÖŞÜçğıöşüâîû]+", parca))
        if not kelimeler:
            continue
        kat = [_fold(k.group()) for k in kelimeler[:4]]
        deger, adet = _yuzde_kelime_oku(kat)
        if not deger:
            continue
        son = m.end() + kayma + kelimeler[adet - 1].end()
        ek = re.match(_YUZDE_SON_EK, ham[son:son + 8], re.I)
        if ek:
            son += ek.end()
        if any(not (e.son <= m.start() or son <= e.bas) for e in bulunan):
            continue
        pencere = ham[max(0, m.start() - 80):son + 40]
        if not _otv_baglami_mi(pencere):
            continue
        bulunan.append(_Eslesme(deger, m.start(), son, "yuzde_kelime"))
    bulunan.sort(key=lambda e: e.bas)
    return bulunan


def metindeki_otv_oranlari(metin: str):
    return [e.oran for e in otv_eslesmeleri(metin)]


def _baska_marka_mi(metin: str, bas: int, son: int, konu_marka: str) -> bool:
    pencere = _fold(str(metin or "")[max(0, bas - 70):son + 30])
    konu = _fold(konu_marka)
    for marka in _BASKA_MARKALAR:
        if marka == konu or (konu and marka in konu):
            continue
        if re.search(rf"\b{marka}\b", pencere):
            return True
    return False


def _ayni_bicim(eslesme, oran: int, kaynak: str) -> str:
    buyuk = kaynak[eslesme.bas:eslesme.son].isupper()
    if eslesme.bicim == "yuzde_isareti_on":
        yeni = f"%{int(oran)}"
    elif eslesme.bicim == "yuzde_isareti_son":
        yeni = f"{int(oran)}%"
    elif eslesme.bicim == "yuzde_kelime":
        yeni = f"yüzde {sayi_yazi(int(oran))}"
    else:
        yeni = f"yüzde {int(oran)}"
    return yeni.upper() if buyuk else yeni


def _aralik_ifadesi(izinli, bicim, buyuk=False) -> str:
    izinli = sorted(int(x) for x in izinli)
    if bicim == "yuzde_kelime":
        govde = " veya ".join(f"yüzde {sayi_yazi(x)}" for x in izinli)
    else:
        govde = " veya ".join(f"%{x}" for x in izinli)
    yeni = f"matrah dilimine göre {govde}"
    return yeni.upper() if buyuk else yeni


def metni_otv_kilidine_cek(metin: str, kilit: dict, marka: str = "") -> str:
    """Kilitli orana uymayan ÖTV yüzdesini değiştirir. Rakip marka penceresine dokunmaz."""
    if not metin or not isinstance(kilit, dict) or not kilit.get("durum"):
        return str(metin or "")
    marka = marka or str(kilit.get("marka") or "")
    eslesmeler = [
        e for e in otv_eslesmeleri(metin)
        if not _baska_marka_mi(metin, e.bas, e.son, marka)
    ]
    if not eslesmeler:
        return metin
    durum = kilit.get("durum")
    if durum == "KESIN":
        try:
            hedef = int(kilit.get("oran"))
        except (TypeError, ValueError):
            return metin
        yeni = metin
        for e in reversed(eslesmeler):
            if e.oran == hedef:
                continue
            yeni = yeni[:e.bas] + _ayni_bicim(e, hedef, metin) + yeni[e.son:]
        return re.sub(r"[ \t]{2,}", " ", yeni)
    if durum == "ARALIK":
        izinli = {int(x) for x in (kilit.get("izinli") or [])}
        bulunan = {e.oran for e in eslesmeler}
        if izinli and bulunan == izinli:
            return metin
        ifade = _aralik_ifadesi(izinli or bulunan, eslesmeler[0].bicim, eslesmeler[0].bicim and metin[eslesmeler[0].bas:eslesmeler[0].son].isupper())
        yeni = metin
        for i, e in enumerate(reversed(eslesmeler)):
            parca = ifade if i == len(eslesmeler) - 1 else ""
            yeni = yeni[:e.bas] + parca + yeni[e.son:]
        return re.sub(r"[ \t]{2,}", " ", yeni).strip()
    if durum == "YASAK":
        yeni = metin
        for e in reversed(eslesmeler):
            buyuk = metin[e.bas:e.son].isupper()
            parca = "RESMİ DİLİMDE, TEK ORAN YOK" if buyuk else "resmi dilimde, tek oran yok"
            yeni = yeni[:e.bas] + parca + yeni[e.son:]
        return re.sub(r"[ \t]{2,}", " ", yeni)
    return metin


def _kimlik(video_state) -> tuple:
    kimlik = (video_state or {}).get("video_identity") if isinstance(video_state, dict) else {}
    kimlik = kimlik if isinstance(kimlik, dict) else {}
    marka = str(kimlik.get("brand") or "").strip()
    model = str(kimlik.get("exact_model") or "").strip()
    variant = str(kimlik.get("variant") or "").strip()
    return marka, model, variant


def _fact_metinleri(fact_state) -> str:
    if not isinstance(fact_state, dict):
        return ""
    parcalar = []
    for alan in ("turkiye_fiyati", "global_fiyat_bilgisi", "arastirma_notu"):
        if fact_state.get(alan):
            parcalar.append(str(fact_state.get(alan)))
    for fact in fact_state.get("facts") or []:
        if isinstance(fact, dict) and not fact.get("_otv_kilidi"):
            parcalar.append(str(fact.get("fact") or ""))
        elif isinstance(fact, str):
            parcalar.append(fact)
    for sinyal in fact_state.get("turkiye_ilgi_sinyalleri") or []:
        if isinstance(sinyal, dict):
            parcalar.extend(str(sinyal.get(k) or "") for k in ("bulgu", "guvenli_anlatim", "neden_turkiyede_ilginc"))
    return "\n".join(p for p in parcalar if p)


def _cc_adaylari(metin: str, model_zorunlu: bool, model: str):
    ham = str(metin or "")
    model_kat = _fold(model)
    for m in re.finditer(r"(\d{3,4})\s*(?:cc|cm\s*3|cm³|cm3)", ham, re.I):
        pencere = ham[max(0, m.start() - 80):m.end() + 40]
        if _esik_mi(pencere):
            continue
        if model_zorunlu and model_kat and model_kat not in _fold(ham[max(0, m.start() - 220):m.end() + 80]):
            continue
        deger = int(m.group(1))
        if 600 <= deger <= 7000:
            yield deger
    for m in re.finditer(r"(\d(?:[.,]\d{1,2})?)\s*(?:litre|lt|l)\b", ham, re.I):
        pencere = ham[max(0, m.start() - 50):m.end() + 50]
        if re.search(r"/100|km/l|l/100", pencere, re.I):
            continue
        if not re.search(r"motor|silindir|hacim|hibrit|hybrid|benzin|dizel|hev|phev", pencere, re.I):
            continue
        if _esik_mi(pencere):
            continue
        if model_zorunlu and model_kat and model_kat not in _fold(ham[max(0, m.start() - 220):m.end() + 80]):
            continue
        try:
            litre = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        if 0.6 <= litre <= 7.0:
            yield int(round(litre * 1000))


def _kw_adaylari(metin: str, model_zorunlu: bool, model: str):
    ham = str(metin or "")
    model_kat = _fold(model)
    kaliplar = (
        r"elektrik(?:li)?\s+motor(?:u|unun)?(?:\s+g[üu]c[üu])?\s*[:\-]?\s*(\d{2,3})\s*kw",
        r"(\d{2,3})\s*kw['’]?(?:l[ıi]k)?\s+elektrik",
        r"e-?motor(?:u)?\s*[:\-]?\s*(\d{2,3})\s*kw",
    )
    for kalip in kaliplar:
        for m in re.finditer(kalip, ham, re.I):
            pencere = ham[max(0, m.start() - 40):m.end() + 40]
            if _esik_mi(pencere):
                continue
            if model_zorunlu and model_kat and model_kat not in _fold(ham[max(0, m.start() - 220):m.end() + 80]):
                continue
            deger = int(next(g for g in m.groups() if g))
            if 10 <= deger <= 400:
                yield deger


def _tip_bul(metin: str) -> str:
    w = _fold(metin)
    if re.search(r"plug-?in|sarj edilebilir|phev", w):
        return "phev"
    if re.search(r"mild|hafif hibrit|48\s*v|mhev", w):
        return "mhev"
    if re.search(r"hibrit|hybrid|\bhev\b", w):
        return "hev"
    if re.search(r"\bbev\b|tam elektrikli|sadece elektrik", w):
        return "bev"
    if re.search(r"benzin|dizel|ic ten yanmali|icten yanmali", w):
        return "ice"
    return "unknown"


def _ortak_sayi(degerler, tolerans):
    degerler = sorted({int(x) for x in degerler if x})
    if not degerler:
        return None
    if max(degerler) - min(degerler) <= tolerans:
        return int(round(sum(degerler) / len(degerler)))
    ozel = [v for v in degerler if v not in _ESIK_CC]
    if ozel and max(ozel) - min(ozel) <= tolerans:
        return int(round(sum(ozel) / len(ozel)))
    return None


def _model_ozel_oranlar(web: str, model: str):
    model_kat = _fold(model)
    if not model_kat or len(model_kat) < 4:
        return []
    oranlar = []
    for cumle in re.split(r"(?<=[.!?])\s+|\n+", str(web or "")):
        if model_kat not in _fold(cumle):
            continue
        bulunan = metindeki_otv_oranlari(cumle)
        if len(set(bulunan)) == 1:
            oranlar.append(bulunan[0])
    return oranlar


def _kilit(durum, **ek):
    govde = {
        "durum": durum,
        "oran": None,
        "izinli": [],
        "gerekce": "",
        "kaynak": OTV_TABLO_KAYNAK,
        "tablo_tarihi": OTV_TABLO_TARIH,
        "tek_oran_yasak": durum != "KESIN",
    }
    govde.update(ek)
    return govde


def _aralik(dusuk, yuksek, matrah, esik, gerekce):
    if matrah is not None:
        oran = dusuk if matrah <= esik else yuksek
        return _kilit("KESIN", oran=oran, izinli=[oran], tek_oran_yasak=False, gerekce=gerekce + f" Matrah {matrah:.0f} TL eşiğin {'altında' if matrah <= esik else 'üstünde'}.")
    return _kilit(
        "ARALIK",
        izinli=[dusuk, yuksek],
        gerekce=gerekce + " Matrah doğrulanmadı; tek oran seçilemez.",
    )


def _ice_orani(cc, matrah):
    if cc > 2000:
        return _kilit("KESIN", oran=220, izinli=[220], tek_oran_yasak=False,
                      gerekce=f"Motor hacmi {cc} cc, 2000 cc eşiğini geçiyor. Bu dilimde ÖTV matrahtan bağımsız %220.")
    if cc > 1600:
        return _aralik(150, 170, matrah, 1_650_000, f"Motor hacmi {cc} cc, 1600-2000 bandı.")
    if cc > 1400:
        if matrah is None:
            return _kilit("ARALIK", izinli=[75, 80, 90, 100],
                          gerekce=f"Motor hacmi {cc} cc, 1400-1600 bandı; matrah olmadan tek oran yok.")
        if matrah <= 850_000:
            oran = 75
        elif matrah <= 1_100_000:
            oran = 80
        elif matrah <= 1_650_000:
            oran = 90
        else:
            oran = 100
        return _kilit("KESIN", oran=oran, izinli=[oran], tek_oran_yasak=False,
                      gerekce=f"Motor hacmi {cc} cc, 1400-1600 bandı, matrah {matrah:.0f} TL.")
    if matrah is None:
        return _kilit("ARALIK", izinli=[70, 75, 80, 90],
                      gerekce=f"Motor hacmi {cc} cc, 1400 cc altı; matrah olmadan tek oran yok.")
    if matrah <= 650_000:
        oran = 70
    elif matrah <= 900_000:
        oran = 75
    elif matrah <= 1_100_000:
        oran = 80
    else:
        oran = 90
    return _kilit("KESIN", oran=oran, izinli=[oran], tek_oran_yasak=False,
                  gerekce=f"Motor hacmi {cc} cc, 1400 cc altı, matrah {matrah:.0f} TL.")


def tablo_orani(tip: str, cc, e_kw, matrah=None) -> dict:
    """Bilinen teknik şartlara göre tek oran, aralık veya yasak döndürür."""
    if tip == "bev":
        if e_kw is None:
            return _kilit("YASAK", gerekce="Tam elektrikli araçta motor gücü (kW) doğrulanmadı; tek ÖTV yüzdesi yazılamaz.")
        if e_kw <= 160:
            return _aralik(25, 55, matrah, 1_650_000, f"Tam elektrikli, {e_kw} kW (160 kW eşiğini geçmiyor).")
        return _aralik(65, 75, matrah, 1_650_000, f"Tam elektrikli, {e_kw} kW (160 kW eşiğini geçiyor).")

    if tip == "phev" and cc is not None and cc <= 1800:
        return _kilit(
            "YASAK",
            gerekce="Şarj edilebilir hibritte özel dilim CO2 ve elektrikli menzile bağlı. İkisi doğrulanmadan %45/%70/%220 satırı seçilemez.",
        )

    if tip == "hev" and cc is not None and e_kw is not None:
        if e_kw > 50 and cc <= 1800:
            return _aralik(70, 80, matrah, 1_250_000,
                           f"Tam hibrit, elektrik motoru {e_kw} kW (>50) ve motor hacmi {cc} cc (≤1800).")
        if e_kw > 100 and cc <= 2500:
            return _aralik(150, 170, matrah, 1_650_000,
                           f"Tam hibrit, elektrik motoru {e_kw} kW (>100) ve motor hacmi {cc} cc (≤2500).")
        return _ice_gerekce_hev(cc, e_kw, matrah)

    if tip == "hev" and cc is not None and cc > 2500:
        return _kilit("KESIN", oran=220, izinli=[220], tek_oran_yasak=False,
                      gerekce=f"Motor hacmi {cc} cc, 2500 cc özel hibrit diliminin dışında. Uygulanan oran %220.")

    if tip == "hev" and cc is not None and cc > 2000 and e_kw is None:
        return _kilit(
            "YASAK",
            gerekce=f"Motor hacmi {cc} cc. 100 kW üstü elektrik motoru varsa %150/%170, yoksa %220. kW doğrulanmadan tek oran yazılamaz.",
        )

    if tip == "hev" and cc is None:
        return _kilit("YASAK", gerekce="Hibrit araçta motor hacmi doğrulanmadı. %70, %170 ve %220 farklı satırlar; biri seçilemez.")

    if tip in {"ice", "mhev"} and cc is not None:
        kilit = _ice_orani(cc, matrah)
        if tip == "mhev":
            kilit["gerekce"] = "Hafif hibrit, içten yanmalı diliminden vergilendirilir. " + kilit["gerekce"]
        return kilit

    if tip == "unknown" and cc is not None and cc > 2000:
        return _kilit("YASAK", gerekce="2000 cc üzeri görünüyor ama hibrit tipi doğrulanmadı. Özel hibrit dilimi ile %220 ayrışmıyor.")

    return _kilit("YASAK", gerekce="ÖTV dilimini kilitleyecek motor hacmi ve güç aktarma tipi doğrulanamadı. Tek yüzde yazılamaz.")


def _ice_gerekce_hev(cc, e_kw, matrah):
    kilit = _ice_orani(cc, matrah)
    kilit["gerekce"] = (
        f"Tam hibrit ama özel dilim şartı yok (elektrik motoru {e_kw} kW, motor hacmi {cc} cc). "
        + kilit["gerekce"]
    )
    return kilit


def otv_kilidi_hesapla(fact_state, web_metni="", video_state=None) -> dict:
    marka, model, variant = _kimlik(video_state)
    fact_blob = "\n".join(p for p in (_fact_metinleri(fact_state), f"{marka} {model} {variant}") if p)
    web = str(web_metni or "")
    cc = _ortak_sayi(
        list(_cc_adaylari(fact_blob, False, model)) + list(_cc_adaylari(web, True, model)),
        80,
    )
    kw = _ortak_sayi(
        list(_kw_adaylari(fact_blob, False, model)) + list(_kw_adaylari(web, True, model)),
        15,
    )
    tip = _tip_bul(fact_blob)
    if tip == "unknown":
        tip = _tip_bul(web if not model else "\n".join(
            c for c in re.split(r"\n+", web) if _fold(model) in _fold(c)
        ))
    kilit = tablo_orani(tip, cc, kw)
    kilit["tip"] = tip
    kilit["motor_hacmi_cc"] = cc
    kilit["elektrik_motor_kw"] = kw
    kilit["marka"] = marka
    model_oranlar = _model_ozel_oranlar(web, model)
    if len(model_oranlar) >= 2 and len(set(model_oranlar)) == 1:
        ozel = model_oranlar[0]
        if kilit["durum"] == "KESIN" and kilit.get("oran") != ozel:
            return _kilit(
                "YASAK",
                gerekce=f"Tablo %{kilit.get('oran')} diyor, modele özel kaynaklar %{ozel} diyor. Çelişki var; tek oran yazılamaz.",
                tip=tip, motor_hacmi_cc=cc, elektrik_motor_kw=kw, marka=marka,
            )
        if kilit["durum"] != "KESIN":
            return _kilit(
                "KESIN", oran=ozel, izinli=[ozel], tek_oran_yasak=False,
                gerekce=f"{model} için birden fazla kaynak aynı ÖTV oranını yazıyor: %{ozel}.",
                kaynak="modele özel web kaynakları + " + OTV_TABLO_KAYNAK,
                tip=tip, motor_hacmi_cc=cc, elektrik_motor_kw=kw, marka=marka,
            )
    return kilit


def vergi_kilidi_talimati(fact_state) -> str:
    kilit = (fact_state or {}).get("vergi_kilidi") if isinstance(fact_state, dict) else None
    if not isinstance(kilit, dict) or not kilit.get("durum"):
        return ""
    if kilit["durum"] == "KESIN":
        return (
            f"\n\n🚨 ÖTV KİLİDİ — BU ÜRETİM İÇİN ZORUNLU: Uygulanan oran %{int(kilit['oran'])}. "
            f"{kilit.get('gerekce') or ''} Kaynak: {kilit.get('kaynak') or OTV_TABLO_KAYNAK}. "
            "Seslendirme, açıklama, kapak ve Threads bu yüzdeden BAŞKA bir ÖTV/vergi yüzdesi YAZAMAZ. "
            "Tablodaki diğer satırları (%70, %150, %170 gibi) bu araca uygulama. "
            "Aynı olguyu platformlar arasında farklı rakamla söylemek yasak.\n"
        )
    if kilit["durum"] == "ARALIK":
        izinli = ", ".join(f"%{int(x)}" for x in (kilit.get("izinli") or []))
        return (
            f"\n\n🚨 ÖTV KİLİDİ: Bu araçta oran matraha göre {izinli} olabilir; TEK ORAN KİLİTLENMEDİ. "
            f"{kilit.get('gerekce') or ''} Tek bir yüzdeyi kesinmiş gibi yazmak YASAK. "
            "Ya oranı hiç söyleme ya da matrah dilimine göre değiştiğini, her iki ucu da söyleyerek yaz. "
            "Seslendirme ile açıklama farklı uç seçemez.\n"
        )
    return (
        "\n\n🚨 ÖTV KİLİDİ: Bu araç için tek bir ÖTV yüzdesi doğrulanamadı. "
        f"{kilit.get('gerekce') or ''} "
        "Web tablosundan bir satır seçip yüzde yazmak YASAK. Vergiden bahsedilecekse oran uydurma; "
        "'resmi dilim araca göre değişir, tek oran kilitlenmedi' de. "
        "Seslendirme bir yüzde, açıklama başka yüzde söyleyemez.\n"
    )


def _kanonik_fact(kilit) -> dict:
    if kilit.get("durum") == "KESIN":
        metin = (
            f"Bu araç için kilitli ÖTV oranı %{int(kilit['oran'])}. "
            f"Başka tablo satırı uygulanmaz. {kilit.get('gerekce') or ''}"
        ).strip()
        durum = "VERIFIED"
    elif kilit.get("durum") == "ARALIK":
        izinli = " veya ".join(f"%{int(x)}" for x in (kilit.get("izinli") or []))
        metin = f"Bu araçta ÖTV matraha göre {izinli} olabilir. Tek oran yazmak yasak. {kilit.get('gerekce') or ''}".strip()
        durum = "VERIFIED"
    else:
        metin = "Bu araç için tek bir ÖTV yüzdesi doğrulanamadı. Vergi yüzdesi yazmak yasak."
        durum = "UNKNOWN"
    return {
        "fact": metin,
        "status": durum,
        "source": kilit.get("kaynak") or OTV_TABLO_KAYNAK,
        "source_type": "official",
        "confidence": "high" if kilit.get("durum") == "KESIN" else "medium",
        "_otv_kilidi": True,
    }


def vergi_kilidini_uygula(fact_state, kilit, log=None) -> dict:
    """Fact Lock metinlerini kilide çeker ve `vergi_kilidi` alanını yazar."""
    state = dict(fact_state or {})
    if not isinstance(kilit, dict):
        return state
    marka = ""
    facts = []
    for fact in state.get("facts") or []:
        if isinstance(fact, dict) and fact.get("_otv_kilidi"):
            continue
        if not isinstance(fact, dict):
            continue
        kopya = dict(fact)
        eski = str(kopya.get("fact") or "")
        yeni = metni_otv_kilidine_cek(eski, kilit, marka)
        if yeni != eski:
            kopya["fact"] = yeni
            kopya["status"] = "VERIFIED" if kilit.get("durum") == "KESIN" else "UNKNOWN"
            kopya["source"] = kilit.get("kaynak") or kopya.get("source") or ""
        facts.append(kopya)
    facts.insert(0, _kanonik_fact(kilit))
    state["facts"] = facts
    sinyaller = []
    for sinyal in state.get("turkiye_ilgi_sinyalleri") or []:
        if not isinstance(sinyal, dict):
            continue
        kopya = dict(sinyal)
        for alan in ("bulgu", "guvenli_anlatim", "neden_turkiyede_ilginc"):
            if kopya.get(alan):
                kopya[alan] = metni_otv_kilidine_cek(kopya.get(alan), kilit, marka)
        sinyaller.append(kopya)
    state["turkiye_ilgi_sinyalleri"] = sinyaller
    state["vergi_kilidi"] = kilit
    if callable(log):
        oran = f"%{kilit.get('oran')}" if kilit.get("oran") else "-"
        log(
            f"🔒 ÖTV kilidi: {kilit.get('durum')} {oran} | "
            f"tip={kilit.get('tip') or '?'} cc={kilit.get('motor_hacmi_cc') or '?'} "
            f"e-kW={kilit.get('elektrik_motor_kw') or '?'} | {str(kilit.get('gerekce') or '')[:180]}"
        )
    return state


def _metin_degisti_log(log, kanal, eski, yeni, kilit):
    if not callable(log) or eski == yeni:
        return
    log(
        f"🔒 ÖTV metni kilide çekildi ({kanal}): {metindeki_otv_oranlari(eski) or ['oran yok']} → "
        f"{metindeki_otv_oranlari(yeni) or ['oran yok']} | kilit={kilit.get('durum')} {kilit.get('oran') or ''}"
    )


def senaryoyu_otv_kilidine_cek(script_state, fact_state, log=None):
    if not isinstance(script_state, dict):
        return script_state or {}
    kilit = (fact_state or {}).get("vergi_kilidi") if isinstance(fact_state, dict) else None
    if not isinstance(kilit, dict) or not kilit.get("durum"):
        return script_state
    marka = ""
    yeni = dict(script_state)
    segments = []
    for seg in script_state.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        kopya = dict(seg)
        eski = str(kopya.get("text") or "")
        kopya["text"] = metni_otv_kilidine_cek(eski, kilit, marka)
        _metin_degisti_log(log, "seslendirme", eski, kopya["text"], kilit)
        segments.append(kopya)
    yeni["segments"] = segments
    eski_soru = str(script_state.get("yorum_tetikleyici_soru") or "")
    yeni["yorum_tetikleyici_soru"] = metni_otv_kilidine_cek(eski_soru, kilit, marka)
    return yeni


def kapaklari_otv_kilidine_cek(basliklar, fact_state, log=None):
    kilit = (fact_state or {}).get("vergi_kilidi") if isinstance(fact_state, dict) else None
    if not isinstance(kilit, dict) or not isinstance(basliklar, list):
        return basliklar
    yeni = []
    for baslik in basliklar:
        if not isinstance(baslik, dict):
            yeni.append(baslik)
            continue
        kopya = dict(baslik)
        for alan in ("ust", "alt", "ana"):
            if kopya.get(alan):
                eski = str(kopya.get(alan))
                kopya[alan] = metni_otv_kilidine_cek(eski, kilit, "")
                _metin_degisti_log(log, f"kapak-{alan}", eski, kopya[alan], kilit)
        yeni.append(kopya)
    return yeni


def sosyal_metni_otv_kilidine_cek(state, alanlar, fact_state, log=None, kanal="sosyal"):
    if not isinstance(state, dict):
        return state
    kilit = (fact_state or {}).get("vergi_kilidi") if isinstance(fact_state, dict) else None
    if not isinstance(kilit, dict):
        return state
    yeni = dict(state)
    for alan in alanlar:
        if yeni.get(alan):
            eski = str(yeni.get(alan))
            yeni[alan] = metni_otv_kilidine_cek(eski, kilit, "")
            _metin_degisti_log(log, kanal, eski, yeni[alan], kilit)
    return yeni


def otv_tutarlilik_sorunlari(reels_state, caption_state, threads_state, fact_state):
    """Kilit dışı veya kanallar arası çelişen ÖTV yüzdelerini döndürür."""
    kilit = (fact_state or {}).get("vergi_kilidi") if isinstance(fact_state, dict) else {}
    kilit = kilit if isinstance(kilit, dict) else {}
    reels = reels_state if isinstance(reels_state, dict) else {}
    caption = caption_state if isinstance(caption_state, dict) else {}
    threads = threads_state if isinstance(threads_state, dict) else {}
    kapak = []
    for baslik in reels.get("kapak_basliklari") or []:
        if isinstance(baslik, dict):
            kapak.append(str(baslik.get("ust") or ""))
            kapak.append(str(baslik.get("alt") or ""))
    kanallar = {
        "seslendirme": str(reels.get("seslendirme_metni") or ""),
        "aciklama": str(caption.get("reels_aciklamasi") or caption.get("reels_aciklama") or ""),
        "threads": str(threads.get("threads_aciklamasi") or ""),
        "kapak": " ".join(kapak),
    }
    oranlar = {ad: metindeki_otv_oranlari(metin) for ad, metin in kanallar.items()}
    sorunlar = []
    if kilit.get("durum") == "KESIN" and kilit.get("oran") is not None:
        hedef = int(kilit["oran"])
        for ad, vals in oranlar.items():
            if any(v != hedef for v in vals):
                sorunlar.append(f"{ad} kilit %{hedef} yerine {vals} diyor")
    elif kilit.get("durum") == "YASAK":
        for ad, vals in oranlar.items():
            if vals:
                sorunlar.append(f"{ad} kilitlenmemiş ÖTV yüzdesi söylüyor: {vals}")
    elif kilit.get("durum") == "ARALIK":
        izinli = {int(x) for x in (kilit.get("izinli") or [])}
        for ad, vals in oranlar.items():
            if not vals:
                continue
            if any(v not in izinli for v in vals) or len(set(vals)) == 1:
                sorunlar.append(f"{ad} matrah belirsizken tek/yanlış oran diyor: {vals}; izinli {sorted(izinli)}")
    dolu = {ad: set(vals) for ad, vals in oranlar.items() if vals}
    if len(dolu) >= 2:
        birlesik = set().union(*dolu.values())
        if len(birlesik) > 1:
            ozet = ", ".join(f"{ad}={sorted(vals)}" for ad, vals in dolu.items())
            sorunlar.append(f"kanallar farklı ÖTV oranı söylüyor ({ozet})")
    # Aynı mesajı iki kez yazma.
    temiz = []
    for sorun in sorunlar:
        if sorun not in temiz:
            temiz.append(sorun)
    return temiz
