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

from core.fiyat_kilidi import tl_tutari_coz

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


def _otv_baglami_mi(pencere: str, oran_konumu=None) -> bool:
    """Aday yüzde ifadesinin en yakın vergi bağlamı ÖTV ise True döndür.

    Yakın bir MTV/KDV ifadesi ile daha uzaktaki ÖTV kelimesini aynı bağlam
    penceresinde görmek, MTV/KDV oranını ÖTV sanmaya yetmemelidir.
    """
    w = _fold(pencere)
    otv_matches = list(re.finditer(r"\botv\b|ozel\s+tuketim|matrah", w))
    diger_matches = list(re.finditer(r"\bmtv\b|\bkdv\b|tasitlar\s+vergisi|motorlu\s+tasit", w))
    if not otv_matches:
        if diger_matches:
            return False
        return "vergi" in w
    if not diger_matches:
        return True
    if oran_konumu is None:
        # İki vergi türü aynı penceredeyse konum bilinmeden güvenli sınıflandırma yok.
        return False

    def _distance(match):
        if match.start() <= oran_konumu <= match.end():
            return 0
        return min(abs(match.start() - oran_konumu), abs(match.end() - oran_konumu))

    en_yakin_otv = min(_distance(match) for match in otv_matches)
    en_yakin_diger = min(_distance(match) for match in diger_matches)
    return en_yakin_otv < en_yakin_diger


def _esik_mi(pencere: str) -> bool:
    return bool(re.search(
        r"geçmeyen|gecmeyen|geçenler|gecenler|aşmayan|asmayan|üzeri|uzeri|"
        r"altı\b|alti\b|altında|altinda|arası|arasi|dilim|kadar",
        _fold(pencere),
    ))


def _baglam_penceresi(ham: str, bas: int, son: int) -> tuple:
    """Aday yüzde ifadesinin bağlam penceresini döndürür.

    Sabit 80/40 karakterlik pencere, ÖTV kilidinin kendi ürettiği uzun kanonik
    ifadeyi ("matrah dilimine göre yüzde yetmiş veya ... yüzde doksan", 90+
    karakter) okuyamıyordu: cümle başındaki 'ÖTV' bağlamı pencerenin dışında
    kalıp ifadenin SON oranları eşleşmeden düşüyordu. Pencere cümle sınırına
    kadar genişletilir; eski 80/40 penceresi de kapsanmaya devam eder.
    """
    geri = bas
    while geri > 0 and ham[geri - 1] not in ".!?\n" and bas - geri < 300:
        geri -= 1
    geri = min(geri, max(0, bas - 80))
    ileri = son
    while ileri < len(ham) and ham[ileri] not in ".!?\n" and ileri - son < 300:
        ileri += 1
    ileri = max(ileri, min(len(ham), son + 40))
    return geri, ileri


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
        pencere_bas, pencere_son = _baglam_penceresi(ham, m.start(), m.end())
        pencere = ham[pencere_bas:pencere_son]
        if not _otv_baglami_mi(pencere, m.start() - pencere_bas):
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
        pencere_bas, pencere_son = _baglam_penceresi(ham, m.start(), son)
        pencere = ham[pencere_bas:pencere_son]
        if not _otv_baglami_mi(pencere, m.start() - pencere_bas):
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


# ÖTV/vergi bağlamı işaretleri: bu kelimeler doğrulanmamış vergi geçişini tanımlar.
_VERGI_BAGLAMI = re.compile(
    r"\bötv\b|özel\s+tüketim|\bmtv\b|matrah|vergi\s+dilimi|vergi\s+oranı|vergi\s+yüzdesi",
    re.I,
)
# Matrah/dilim jargonu tek başına bile vergi tartışmasıdır (KESIN değilse yasak).
_MATRAH_ISARETI = re.compile(r"matrah|vergi\s+dilim|vergi\s+orani|vergi\s+oranı", re.I)
_YUZDE_ISARETI = re.compile(r"%\s*\d|\d\s*%|y[üu]zde\s+(?:\d|[a-zçğıöşü])", re.I)
# Silme sonrası "kalan" cümlede içerik olduğunu gösteren işaret (fiyat, cc, kW...).
_ICERIK_ISARETI = re.compile(r"\d|TL|₺|\bEuro\b|\bDolar\b|€|\$", re.I)
# Cümle/yan cümle sınırları: ÖTV geçişi bu sınırların arasından çıkarılır.
_YAN_CUMLE_SINIRI = ",.!?\n;"
# Eski ARALIK yazımının bıraktığı ikilemeleri de temizle ("... arasında ile arasında").
_IKILEME = re.compile(r"(\s+arasında)(?:\s+(?:ile|veya)\s+arasında)+", re.I)

_BAGLAC_SONU = re.compile(r"\s+(?:ve|veya|ya\s*da|ama|ancak|ile|ise)\s*$", re.I)
_BAGLAC_BASI = re.compile(r"^\s*(?:ve|veya|ya\s*da|ama|ancak|ise)\s+", re.I)

# Vergi geçişinin sağındaki "dolgu" kelimeleri: "%75 ile %100 ARASINDA DEĞİŞİYOR",
# "%80 ORANINDA UYGULANIYOR" kuyrukları silinirken yutulur; "… ve fiyatı" gibi
# içerik başlatan kelimede tarama durur ve içerik korunur.
_VERGI_KUYRUK = {
    "arasinda", "arasi", "araliginda", "bandinda", "civarinda", "seviyesinde",
    "oran", "orani", "oraninda", "oranlari", "oranlarinda", "oranlar",
    "dilim", "dilimi", "dilimine", "diliminde", "dilimleri", "dilimlerine",
    "matrah", "matrahi", "matraha", "matrahin", "matrahta",
    "degisir", "degisiyor", "degisebilir", "degisken",
    "olabilir", "olur", "olacak", "oluyor", "oldugu", "olan",
    "uygulanir", "uygulaniyor", "uygulanacak", "uygulanan",
    "hesaplanir", "hesaplaniyor", "hesaplanacak",
    "kadar", "yukselebilir", "yukselebiliyor", "cikabilir", "cikabiliyor",
    "gecerli", "gecer", "tabi", "sahip", "dahil",
    "ile", "ve", "veya", "ya", "da", "de", "ise", "gore", "olarak",
    "yuzde", "tek", "bir", "iki", "uc", "dort",
    "vergi", "vergiye", "vergisi", "verginin", "otv", "ozel", "tuketim",
}


def _sinir_mi(metin: str, konum: int) -> bool:
    """Konumdaki karakter cümle/yan cümle sınırı mı?

    Sayı içindeki nokta sınır değildir: '2.349.000 TL' veya '1.6 motor'
    ifadesinde ondalık/binlik ayırıcı nokta cümleyi bölmez.
    """
    if konum < 0 or konum >= len(metin):
        return False
    karakter = metin[konum]
    if karakter not in _YAN_CUMLE_SINIRI:
        return False
    if karakter == ".":
        # Yalnız iki yanında rakam olan nokta sayı ayırıcıdır ("2.349.000",
        # "1.6 litre"); "%80. Motor" ya da "TL. Bakalım" cümle sonudur.
        onceki = metin[konum - 1] if konum > 0 else ""
        sonraki = metin[konum + 1] if konum + 1 < len(metin) else ""
        if onceki.isdigit() and sonraki.isdigit():
            return False
    return True


def _cevre_marka_mi(metin: str, bas: int, son: int, konu_marka: str) -> bool:
    """Eşleşmenin bulunduğu YAN CÜMLEDE başka marka geçiyor mu?

    Sabit ±70/+30 karakterlik pencere, aynı cümlede iki marka geçtiğinde
    ("BMW X1 %80, Kia Seltos %75") konu markanın oranını da rakip sayıp
    düzenlemeyi atlıyordu; pencere yan cümleyle sınırlandırılır.
    """
    geri = bas
    while geri > 0 and not _sinir_mi(metin, geri - 1):
        geri -= 1
    ileri = son
    while ileri < len(metin) and not _sinir_mi(metin, ileri):
        ileri += 1
    return _baska_marka_mi(metin[geri:ileri], bas - geri, son - geri, konu_marka)


def _cumle_araligi(metin: str, bas: int, son: int) -> tuple:
    geri = bas
    while geri > 0 and not _sinir_mi(metin, geri - 1):
        geri -= 1
    ileri = son
    while ileri < len(metin) and not _sinir_mi(metin, ileri):
        ileri += 1
    return geri, ileri


def _kuyruk_sonu(metin: str, konum: int) -> int:
    """Yüzde ifadesinden sonra kalan vergi dolgusunu yutar, içerikte durur."""
    son = konum
    while son < len(metin):
        if _sinir_mi(metin, son):
            break
        harf = metin[son]
        if harf.isspace() or harf in ",;:":
            son += 1
            continue
        kelime_eslesmesi = re.match(r"[A-Za-zÇĞİÖŞÜçğıöşüâîû]+", metin[son:])
        if not kelime_eslesmesi:
            # Yüzde işareti/rakam/ayraç: kuyruk sürüyor.
            son += 1
            continue
        if _fold(kelime_eslesmesi.group()) not in _VERGI_KUYRUK:
            break
        son += kelime_eslesmesi.end()
    return son


def _gecis_araligi(metin: str, bas: int, son: int) -> tuple:
    """ÖTV geçişinin silinecek aralığı.

    * Sol sınır cümle/yan cümle başıdır; geçişten önce somut veri (fiyat, cc)
      varsa silme vergi kelimesinden başlar ve öncesindeki bağlaç düşer
      ("Fiyatı 2.349.000 TL ve ÖTV %75" → "Fiyatı 2.349.000 TL").
    * Sağ sınır vergi dolgusu kelimelerinin (arasında, değişiyor, uygulanıyor,
      olabilir...) bittiği yerdir; içerik başlarsa ("… ve fiyatı 2.349.000 TL")
      içerik korunur (bkz. _kuyruk_sonu).
    """
    geri = bas
    while geri > 0 and not _sinir_mi(metin, geri - 1):
        geri -= 1
    ileri = _kuyruk_sonu(metin, son)
    span = metin[geri:bas]
    ilk = _VERGI_BAGLAMI.search(span)
    if ilk and re.search(r"\d", span[:ilk.start()]):
        geri = geri + ilk.start()
        baglac = _BAGLAC_SONU.search(metin[:geri])
        if baglac:
            geri = baglac.start()
    else:
        # Geçişten önceki kısımda içerik yoksa (ör. "Kia Seltos için ÖTV %75
        # diyenler var.") cümlenin tamamı vergi tartışmasıdır: sağda kalan
        # içeriksiz parça da silinir.
        cumle_geri, cumle_ileri = _cumle_araligi(metin, bas, son)
        kalan = metin[cumle_geri:geri] + metin[ileri:cumle_ileri]
        if not _ICERIK_ISARETI.search(kalan):
            geri, ileri = cumle_geri, cumle_ileri
    return geri, ileri


def _vergi_cumlesi_mi(cumle: str) -> bool:
    """Vergiden söz eden cümle, tek oran doğrulanmadığı için silinmeli mi?

    * Matrah/dilim jargonu: her zaman (matrah doğrulanmadıysa geçmemeli).
    * Yüzde iddiası (rakam ya da yazıyla): her zaman.
    * Rakamsız kısa vergi dolgusu ("ÖTV tarafı belirsiz"): silinir.
    Fiyat/motor hacmi gibi rakamlı içerik cümlesi korunur.
    """
    metin = str(cumle or "").strip()
    if not metin or not _VERGI_BAGLAMI.search(metin):
        return False
    if _MATRAH_ISARETI.search(metin) or _YUZDE_ISARETI.search(metin):
        return True
    return not re.search(r"\d", metin) and len(metin.split()) <= 14


def _noktalama_temizle(metin: str) -> str:
    """Silme sonrası kalan boşluk/çift noktalama artıklarını düzeltir."""
    yeni = _IKILEME.sub(r"\1", str(metin or ""))
    yeni = re.sub(r"[ \t]{2,}", " ", yeni)
    yeni = re.sub(r"\s+([,.;:!?])", r"\1", yeni)
    yeni = re.sub(r"([,;:])\s*([.!?])", r"\2", yeni)
    yeni = re.sub(r"([,;:])(?=\s*[,;:.!?])", "", yeni)
    # Silme sonrası ikilenen nokta ("TL.. ") temizlenir; üç nokta (...) korunur.
    yeni = re.sub(r"(?<!\.)\.\.(?!\.)", ".", yeni)
    yeni = re.sub(r"^[\s,;:.]+", "", yeni)
    yeni = re.sub(r"^(?:ise|ancak|ama|fakat|ve|veya)\s+", "", yeni, flags=re.I)
    yeni = re.sub(r"\s+(?:için|ile|göre|dair|hakkında|üzere)\s*([.!?,;:])", r"\1", yeni)
    yeni = re.sub(r"[,;:]+$", "", yeni)
    # Virgülden sonra boşluk düşmüşse geri koy (ondalık virgül "1,5" korunur).
    yeni = re.sub(r",(?=[A-Za-zÇĞİÖŞÜçğıöşü])", ", ", yeni)
    yeni = yeni.strip()
    if yeni[:1].islower():
        yeni = yeni[:1].upper() + yeni[1:]
    return yeni


def otv_gecisini_temizle(metin: str, marka: str = "") -> str:
    """Metinden doğrulanmamış ÖTV/vergi geçişini TAMAMEN çıkarır.

    Kilit KESIN değilken (matrah/oran doğrulanmadı) ÖTV'den hiç söz etmemek
    esastır: tek yüzde yazmak da "matraha göre değişir" demek de yasaktır.
    Yüzde içeren yan cümleler ile vergi dolgusu cümleleri silinir; fiyat,
    motor hacmi gibi diğer olgular korunur. Rakip marka penceresine
    dokunulmaz. İşlev idempotenttir.
    """
    if not metin:
        return str(metin or "")
    sonuc = str(metin)
    # 1) Yüzde içeren vergi geçişleri. Sondan başa silinir ve her turda
    #    eşleşmeler YENİDEN hesaplanır: bir cümlede iki yüzde varsa ilk silme
    #    ikisini birden götürür, eski indekslerle ikinci silme metni bozardı.
    while True:
        eslesmeler = [
            e for e in otv_eslesmeleri(sonuc)
            if not _cevre_marka_mi(sonuc, e.bas, e.son, marka)
        ]
        if not eslesmeler:
            break
        eslesme = eslesmeler[-1]
        bas, son = _gecis_araligi(sonuc, eslesme.bas, eslesme.son)
        yeni = sonuc[:bas] + _BAGLAC_BASI.sub("", sonuc[son:])
        if yeni == sonuc:
            break
        sonuc = yeni
    # 2) Kalan vergi YAN CÜMLELERİ (matrah jargonu, yüzde iddiası, rakamsız
    #    dolgu). Cümle bazında değil yan cümle bazında silinir; böylece aynı
    #    cümledeki içerik ("… , fiyatı 2.349.000 TL.") korunur. Fragman cümle
    #    sonu noktalamasıyla bitiyorsa noktalama yerinde bırakılır.
    parcalar = []
    bas = 0
    for i in range(len(sonuc)):
        if sonuc[i] in ",;:\n" or (sonuc[i] in ".!?" and _sinir_mi(sonuc, i)):
            parca = sonuc[bas:i + 1]
            if parca.strip() and _vergi_cumlesi_mi(parca) and not _baska_marka_mi(sonuc, bas, i + 1, marka):
                if sonuc[i] in ".!?":
                    parcalar.append(sonuc[i])
            else:
                parcalar.append(parca)
            bas = i + 1
    if bas < len(sonuc):
        parcalar.append(sonuc[bas:])
    sonuc = "".join(parcalar)
    return _noktalama_temizle(sonuc)


def metni_otv_kilidine_cek(metin: str, kilit: dict, marka: str = "") -> str:
    """Metni ÖTV kilidine çeker. Rakip marka penceresine dokunmaz.

    * KESIN kilit: kilit dışı yüzdeler kilitli orana çevrilir.
    * ARALIK / YASAK kilit: tek oran kilitlenmediği için vergi geçişi metinden
      ÇIKARILIR. (Eski davranış kanonik "matrah dilimine göre %75 ile %100
      arasında" ifadesini yazıyordu; bu ifade ikinci uygulamada "arasında ile
      arasında" ikilemesi üretiyor ve 04.10.2026'da açıklamayı bozuyordu.)
    Bu işlev idempotenttir: aynı metne ikinci kez uygulanması çıktıyı değiştirmez.
    """
    if not metin or not isinstance(kilit, dict) or not kilit.get("durum"):
        return str(metin or "")
    marka = marka or str(kilit.get("marka") or "")
    durum = kilit.get("durum")
    if durum == "KESIN":
        eslesmeler = [
            e for e in otv_eslesmeleri(metin)
            if not _cevre_marka_mi(metin, e.bas, e.son, marka)
        ]
        if not eslesmeler:
            return metin
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
    if durum in {"ARALIK", "YASAK"}:
        return otv_gecisini_temizle(metin, marka)
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


# Araç için AÇIKÇA belirtilmiş matrah ("matrahı 1.000.000 TL"). Tablo eşik
# değerleri ("matrahı 850.000 TL'ye kadar olanlar") matrah DEĞİLDİR; _esik_mi
# ve model bağlamı korumasıyla ayıklanır. Satış fiyatı da matrah değildir
# (matrah vergi tabanıdır) — bu yüzden yalnızca 'matrah' kelimesiyle geçen
# tutarlar kullanılır.
_MATRAH_TUTAR = re.compile(
    r"matrah\w{0,5}\s+(?:\S+\s+){0,2}?(\d[\d.,]*)\s*((?:bin|milyon))?\s*(?:TL|₺|lira)",
    re.I,
)
_MATRAH_ALT = 100_000
_MATRAH_UST = 20_000_000


def _matrah_adaylari(metin: str, model_zorunlu: bool, model: str):
    ham = str(metin or "")
    model_kat = _fold(model)
    for m in _MATRAH_TUTAR.finditer(ham):
        pencere = ham[max(0, m.start() - 90):m.end() + 60]
        if _esik_mi(pencere):
            continue
        if model_zorunlu and model_kat and model_kat not in _fold(ham[max(0, m.start() - 220):m.end() + 80]):
            continue
        deger = tl_tutari_coz(m.group(1), m.group(2) or "")
        if deger is not None and _MATRAH_ALT <= deger <= _MATRAH_UST:
            yield deger


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
    # Matrah yalnızca açıkça yazılmışsa kullanılır (tablo eşikleri ve satış
    # fiyatı matrah sayılmaz). Matrah bilinirse ARALIK tek orana iner.
    matrah = _ortak_sayi(
        list(_matrah_adaylari(fact_blob, False, model)) + list(_matrah_adaylari(web, True, model)),
        80,
    )
    tip = _tip_bul(fact_blob)
    if tip == "unknown":
        tip = _tip_bul(web if not model else "\n".join(
            c for c in re.split(r"\n+", web) if _fold(model) in _fold(c)
        ))
    kilit = tablo_orani(tip, cc, kw, matrah)
    kilit["tip"] = tip
    kilit["motor_hacmi_cc"] = cc
    kilit["elektrik_motor_kw"] = kw
    kilit["matrah_tl"] = matrah
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
    # ARALIK ve YASAK: tek oran kilitlenmedi. Bu durumda içerikte ÖTV'den HİÇ
    # söz edilmez (04.10.2026 kullanıcı geri bildirimi: "net ÖTV ve matrah yoksa
    # bundan bahsetmek zorunda değiliz"). Ne tek yüzde ne "matraha göre değişir"
    # dolgusu yazılır; kelime/karakter bütçesi doğrulanmış fiyat ve teknik
    # verilere ayrılır. Kilidi uygulayan deterministik temizlik katmanı da
    # yüzde/dolgu cümlelerini metinden çıkarır (bkz. otv_gecisini_temizle).
    durum = kilit["durum"]
    gerekce = str(kilit.get("gerekce") or "")
    if durum == "ARALIK":
        return (
            "\n\n🚨 ÖTV KİLİDİ: Bu araçta ÖTV oranı matrah bilinmediği için TEK ORANA "
            f"KİLİTLENEMEDİ. {gerekce} "
            "Bu üretimde ÖTV/vergi oranından HİÇ SÖYLEME: yüzde yazmak da, "
            "'matraha göre değişir' demek de YASAK. Vergiden hiç bahsetmemek doğrudur "
            "ve QA'da FAIL değildir. Kelime/karakter bütçesini doğrulanmış fiyat, donanım "
            "ve somut teknik verilere ayır.\n"
        )
    return (
        "\n\n🚨 ÖTV KİLİDİ: Bu araç için tek bir ÖTV yüzdesi doğrulanamadı. "
        f"{gerekce} "
        "Bu üretimde ÖTV/vergi oranından HİÇ SÖYLEME: tablodan satır seçip yüzde yazmak da, "
        "'orana göre değişir' demek de YASAK. Vergiden hiç bahsetmemek doğrudur ve QA'da "
        "FAIL değildir. Doğrulanmış fiyat ve teknik verilere odaklan.\n"
    )


def _kanonik_fact(kilit) -> dict:
    if kilit.get("durum") == "KESIN":
        metin = (
            f"Bu araç için kilitli ÖTV oranı %{int(kilit['oran'])}. "
            f"Başka tablo satırı uygulanmaz. {kilit.get('gerekce') or ''}"
        ).strip()
        durum = "VERIFIED"
    elif kilit.get("durum") == "ARALIK":
        # Matrah doğrulanmadığı için tek oran kilitlenemedi. Kanonik Fact Lock
        # kaydı da oran/dilim taşımaz (04.10.2026: iki uçlu "%75 ile %100
        # arasında" ifadesi üretim ajanlarına vergi cümlesi yazdırıyor, hatta
        # kanal kilidi ikinci uygulamada metni bozuyordu). Status UNKNOWN'dır:
        # içerikte vergi oranı kullanılamaz, vergiden hiç söz edilmez.
        metin = (
            "Bu araçta ÖTV oranı doğrulanamadı (matrah bilinmiyor, tek oran kilitlenmedi). "
            f"İçerikte ÖTV/vergi oranından söz edilmez. {kilit.get('gerekce') or ''}"
        ).strip()
        durum = "UNKNOWN"
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
    eski_oranlar = metindeki_otv_oranlari(eski) or ["oran yok"]
    yeni_oranlar = metindeki_otv_oranlari(yeni) or ["oran yok"]
    if kilit.get("durum") in {"ARALIK", "YASAK"}:
        # KESIN olmayan kilitte vergi geçişi silinir; log bunu açıkça söyler.
        log(
            f"🧹 ÖTV/vergi geçişi çıkarıldı ({kanal}): {eski_oranlar} → {yeni_oranlar} "
            f"| kilit={kilit.get('durum')} (doğrulanmış tek oran yok; vergiden söz edilmez)"
        )
        return
    log(
        f"🔒 ÖTV metni kilide çekildi ({kanal}): {eski_oranlar} → "
        f"{yeni_oranlar} | kilit={kilit.get('durum')} {kilit.get('oran') or ''}"
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
        # 04.10.2026 politikası: matrah/oran doğrulanmadıysa içerikte ÖTV
        # oranından HİÇ söz edilmez. Artık tek yanlış oran da, kanonik iki uç
        # ("%75 ile %100") da kanal başına işaretlenir; kilit katmanı bu
        # geçişleri deterministik olarak siler (bkz. otv_gecisini_temizle).
        for ad, vals in oranlar.items():
            if vals:
                sorunlar.append(
                    f"{ad} matrah belirsizken ÖTV oranı söylüyor: {vals}; "
                    "doğrulanmış tek oran yok, vergiden hiç söz edilmemeli"
                )
    dolu = {ad: set(vals) for ad, vals in oranlar.items() if vals}
    if len(dolu) >= 2:
        # İki kanalın iddiası yalnız kümelerden biri diğerini KAPSAYARAK uyumludur.
        # Eski "birleşim > 1" kontrolü yasal ARALIK üretimini bile hata sayıp
        # üretimi durduruyordu: talimat "her iki ucu da söyle" dediği için
        # seslendirme {70, 90}, kilitlenen açıklama tüm dilimleri {70,75,80,90}
        # söylüyordu — çelişki yok. Gerçek çelişki, kümelerin karşılaştırılamaz
        # olmasıdır (ör. {70, 80} ile {75, 90}; veya {70} ile {90}).
        adlar = list(dolu)
        celiski = False
        for i in range(len(adlar)):
            for j in range(i + 1, len(adlar)):
                a, b = dolu[adlar[i]], dolu[adlar[j]]
                if not (a <= b or b <= a):
                    celiski = True
                    break
            if celiski:
                break
        if celiski:
            ozet = ", ".join(f"{ad}={sorted(vals)}" for ad, vals in dolu.items())
            sorunlar.append(f"kanallar farklı ÖTV oranı söylüyor ({ozet})")
    # Aynı mesajı iki kez yazma.
    temiz = []
    for sorun in sorunlar:
        if sorun not in temiz:
            temiz.append(sorun)
    return temiz
