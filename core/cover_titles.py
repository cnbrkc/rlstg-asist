"""Reels kapak yazısı katmanı — [Kural: Reels Kapak Yazısı Formatı].

Kullanıcı kuralı (istisnasız):
  * Her üretimde TAM 5 farklı kapak alternatifi sunulur; tek başlık asla yeterli değildir.
  * Her alternatif iki katmandan oluşur:
      - ÜST BAŞLIK : dikkat çekici kanca, TAMAMI BÜYÜK HARF, 2-4 kelime
                     (amaç: kaydırmayı durdurmak / merak yaratmak).
      - Alt başlık : tamamlayıcı detay, cümle düzeni (yalnızca ilk harf büyük), 4-7 kelime
                     (amaç: merakı açıklamak / izlemek için sebep vermek).

Hook Generator ajanı bu seti üretir; burada çıktı deterministik olarak
doğrulanır ve düzeltilir (Türkçe büyük/küçük harf, kelime sınırları, tekrar).
Ajan eksik/boş dönerse Editorial/Detective kanıtlarından yerel alternatiflerle
5'e tamamlanır. Böylece API yoğunluğunda bile Telegram'a asla tek başlık gitmez.
"""
import math
import re

ALTERNATIF_SAYISI = 5
UST_MIN_KELIME, UST_MAX_KELIME = 2, 4
ALT_MIN_KELIME, ALT_MAX_KELIME = 4, 7

_TR_BUYUK = str.maketrans("iıöüçşğâîû", "İIÖÜÇŞĞÂÎÛ")
_TR_KUCUK = str.maketrans("IİÖÜÇŞĞÂÎÛ", "ıiöüçşğâîû")
_KELIME_RE = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*|\d[\d.,%]*", re.UNICODE)
_BOSLUK_RE = re.compile(r"\s+")
_TIRNAK_RE = re.compile(r"[\"“”„«»]")
_CUMLE_AYRAC_RE = re.compile(r"(?<=[.!?…;:])\s+|\s+[–—-]\s+|,\s+")

# Cümle düzenine çevirirken BÜYÜK kalacak kısaltmalar ve baş harfi büyük kalacak özel isimler.
_KISALTMALAR = {
    "abd", "ab", "ötv", "kdv", "mtv", "tl", "usd", "eur", "suv", "ev", "phev", "hev", "mhev", "abs", "esp",
    "led", "gps", "usb", "bmw", "byd", "mg", "hp", "bg", "nm", "kw", "kwh", "tsi", "tdi", "dsg", "awd",
    "fwd", "rwd", "cvt", "hud", "lpg", "cng", "vw", "amg", "gti", "gtd", "rs", "sd", "obd", "adas", "ncap",
}
_OZEL_ISIMLER = {
    "türkiye", "avrupa", "almanya", "fransa", "italya", "ingiltere", "amerika", "çin", "japonya", "kore",
    "hindistan", "rusya", "ispanya", "hollanda", "istanbul", "ankara", "izmir", "bursa", "euro", "dolar",
    "tesla", "togg", "mercedes", "audi", "volkswagen", "toyota", "honda", "hyundai", "kia", "renault", "fiat",
    "peugeot", "citroën", "citroen", "opel", "ford", "skoda", "seat", "cupra", "volvo", "porsche", "ferrari",
    "lamborghini", "chery", "dacia", "nissan", "mazda", "subaru", "lexus", "jeep", "mini", "suzuki",
    "mitsubishi", "tofaş", "xiaomi", "leapmotor", "omoda", "jaecoo", "lynk", "polestar", "smart", "bentley",
    "rolls", "royce", "maserati", "alfa", "romeo", "land", "rover", "range", "jaguar", "tata", "geely",
    "google", "apple", "android", "carplay",
}

# Yerel yedek şablonlar (yalnızca ajan eksik döndüğünde; hepsi kurala uygun).
_YEDEK_UST = (
    "BUNU KİMSE SÖYLEMEDİ",
    "GERÇEK RAKAM ŞOK",
    "HERKES YANLIŞ BİLİYOR",
    "ALMADAN ÖNCE İZLE",
    "DETAY HER ŞEYİ DEĞİŞTİRİR",
    "KİMSE BEKLEMİYORDU",
    "İŞTE ASIL MESELE",
)
_YEDEK_ALT = (
    "Videoda anlatılan detay hesabı değiştiriyor",
    "Türkiye fiyatına bakınca tablo farklı",
    "Kağıt üzerindeki rakam gerçeği anlatmıyor",
    "Kullanıcıların asıl şikayet ettiği nokta bu",
    "Bu kararı vermeden önce bunu bil",
    "Herkesin konuştuğu asıl fark burada",
    "Sonuna kadar izleyince mesele netleşiyor",
)


def tr_buyuk(metin: str) -> str:
    """Türkçe uyumlu büyük harf (i → İ, ı → I)."""
    return str(metin or "").translate(_TR_BUYUK).upper()


def tr_kucuk(metin: str) -> str:
    """Türkçe uyumlu küçük harf (I → ı, İ → i)."""
    return str(metin or "").translate(_TR_KUCUK).lower()


def _temizle(metin: str) -> str:
    metin = _TIRNAK_RE.sub("", str(metin or ""))
    metin = metin.replace("\n", " ").strip(" \t-–—:;•*#")
    return _BOSLUK_RE.sub(" ", metin).strip()


def kelimeler(metin: str) -> list:
    return _KELIME_RE.findall(str(metin or ""))


def kelime_sayisi(metin: str) -> int:
    return len(kelimeler(metin))


def _kelime_kirp(metin: str, maksimum: int) -> str:
    """Boşlukla ayrılmış token'ları koruyarak ilk `maksimum` kelimeyi bırakır."""
    secilen, sayac = [], 0
    for parca in _temizle(metin).split(" "):
        if sayac >= maksimum:
            break
        secilen.append(parca)
        if kelime_sayisi(parca):
            sayac += 1
    return " ".join(secilen).strip(" ,;:-–—")


def _bas_harf_buyuk(kelime: str) -> str:
    for i, ch in enumerate(kelime):
        if ch.isalpha():
            return kelime[:i] + tr_buyuk(ch) + kelime[i + 1:]
    return kelime


def _kok(soz: str) -> str:
    govde = re.sub(r"[^\w'’-]", "", soz)
    return tr_kucuk(re.split(r"['’]", govde, maxsplit=1)[0])


def _tam_buyuk(soz: str) -> bool:
    harfler = [c for c in soz if c.isalpha()]
    return len(harfler) >= 2 and all(c == tr_buyuk(c) for c in harfler)


def _kisaltma_duzenle(soz: str) -> str:
    m = re.search(r"['’]", soz)
    if not m:
        return tr_buyuk(soz)
    return tr_buyuk(soz[:m.start()]) + tr_kucuk(soz[m.start():])


def _cumle_duzeni(metin: str) -> str:
    """Yalnızca ilk harf büyük. BAĞIRAN ve Başlık Düzeni kelimeler küçültülür;
    kısaltmalar (ÖTV, SUV...) büyük, özel isimler (Türkiye, Tesla...) baş harfi büyük kalır."""
    metin = _temizle(metin).rstrip(".!?…")
    if not metin:
        return ""
    sozler = metin.split(" ")
    harfli = [s for s in sozler if any(c.isalpha() for c in s)]
    bas_buyuk = sum(1 for s in harfli if _bas_harf_buyuk(s) == s)
    baslik_duzeni = len(harfli) >= 3 and bas_buyuk >= max(3, math.ceil(len(harfli) * 0.7))
    cikti = []
    for i, soz in enumerate(sozler):
        kok = _kok(soz)
        if kok in _KISALTMALAR:
            yeni = _kisaltma_duzenle(soz)
        elif kok in _OZEL_ISIMLER:
            yeni = _bas_harf_buyuk(tr_kucuk(soz))
        elif any(c.isdigit() for c in soz):
            yeni = soz  # T10X, ID.4, 4x4 gibi model kodlarına dokunma
        elif _tam_buyuk(soz) or baslik_duzeni:
            yeni = tr_kucuk(soz)
        else:
            yeni = soz
        if i == 0 and kok not in _KISALTMALAR:
            yeni = _bas_harf_buyuk(yeni)
        cikti.append(yeni)
    return " ".join(cikti)


def ust_baslik_duzenle(metin: str) -> str:
    """ÜST BAŞLIK: 2-4 kelime, TAMAMI BÜYÜK HARF; noktalama sadeleştirilir."""
    metin = _temizle(metin).rstrip(".…:;,!")
    if not metin:
        return ""
    if kelime_sayisi(metin) > UST_MAX_KELIME:
        metin = _kelime_kirp(metin, UST_MAX_KELIME)
    return tr_buyuk(metin).rstrip(".…:;,")


# Kırpılmış bir alt başlık bu kelimelerle BİTEMEZ (yarım cümle hissi verir).
_SARKIK_SON_KELIMELER = {
    "ve", "ile", "için", "ama", "fakat", "ancak", "da", "de", "ki", "bir", "veya", "ya", "yada",
    "gibi", "olan", "en", "çok", "daha", "hem", "ne", "şu", "bu", "o", "her", "tüm", "kadar",
}
_TEK_TIRNAK_RE = re.compile(r"(^|\s)['‘’`]+|['‘’`]+(?=\s|$)")


def _kirpik_sonu_temizle(metin: str) -> str:
    """Kelime sınırından kırpılmış metnin sonundaki sarkık bağlaç/edatları ve
    eşleşmemiş tek tırnakları temizler ("... ve 'teknolojik" → "...")."""
    metin = _BOSLUK_RE.sub(" ", _TEK_TIRNAK_RE.sub(lambda m: m.group(1) or "", metin)).strip(" ,;:-–—")
    sozler = metin.split(" ")
    while sozler and tr_kucuk(re.sub(r"[^\w]", "", sozler[-1])) in _SARKIK_SON_KELIMELER:
        sozler.pop()
    return " ".join(sozler).strip(" ,;:-–—")


def alt_baslik_duzenle(metin: str) -> str:
    """Alt başlık: 4-7 kelime, cümle düzeni (yalnızca ilk harf büyük)."""
    metin = _cumle_duzeni(metin)
    if not metin:
        return ""
    if kelime_sayisi(metin) > ALT_MAX_KELIME:
        metin = _cumle_duzeni(_kirpik_sonu_temizle(_kelime_kirp(metin, ALT_MAX_KELIME)))
    return metin


def ust_kurala_uygun_mu(metin: str) -> bool:
    metin = str(metin or "")
    return bool(metin) and UST_MIN_KELIME <= kelime_sayisi(metin) <= UST_MAX_KELIME and metin == tr_buyuk(metin)


def alt_kurala_uygun_mu(metin: str) -> bool:
    metin = str(metin or "")
    if not metin or not (ALT_MIN_KELIME <= kelime_sayisi(metin) <= ALT_MAX_KELIME):
        return False
    return metin != tr_buyuk(metin) and metin[:1] == tr_buyuk(metin[:1])


def _anahtar(metin: str) -> str:
    return " ".join(tr_kucuk(k) for k in kelimeler(metin))


def _aday_ciftler(ham) -> list:
    """LLM çıktısını (list / dict / str) (ust, alt) çiftlerine indirger."""
    if isinstance(ham, dict):
        ham = ham.get("kapak_basliklari") or ham.get("alternatifler") or [ham]
    if isinstance(ham, str):
        ham = [ham]
    ciftler = []
    for item in ham or []:
        if isinstance(item, dict):
            ust = item.get("ust") or item.get("ana") or item.get("kapak_ana") or item.get("ust_baslik") or item.get("baslik") or item.get("kapak_metni") or ""
            alt = item.get("alt") or item.get("kapak_alt") or item.get("alt_baslik") or item.get("aciklama") or ""
        elif isinstance(item, (list, tuple)) and item:
            ust = item[0]
            alt = item[1] if len(item) > 1 else ""
        else:
            ust, alt = item, ""
        ust, alt = _temizle(ust), _temizle(alt)
        if not ust and not alt:
            continue
        if ust and not alt:
            # "ÜST / alt", "ÜST — alt", "ÜST: alt" tek satır geldiyse ayır.
            for ayrac in (" / ", " — ", " – ", " | ", ": "):
                if ayrac in ust:
                    parca_ust, parca_alt = ust.split(ayrac, 1)
                    if kelime_sayisi(parca_ust) <= UST_MAX_KELIME and kelime_sayisi(parca_alt) >= 2:
                        ust, alt = parca_ust, parca_alt
                    break
        ciftler.append((ust, alt))
    return ciftler


def _kaynak_metinleri(editorial_state, detective_state=None, hook_state=None) -> list:
    """Yerel alternatifler için kanıt cümleleri (öncelik sırasıyla)."""
    kaynaklar = []
    for state in (hook_state or {}, detective_state or {}, editorial_state or {}):
        if not isinstance(state, dict):
            continue
        for anahtar in ("ilk_3_saniye_kanca", "viral_kan_mali", "turkiye_ozel_magduriyet", "core_story",
                        "why_it_matters", "audience_trigger", "discussion_territory"):
            deger = state.get(anahtar)
            if isinstance(deger, str) and deger.strip():
                kaynaklar.append(deger)
        for anahtar in ("kronik_sikayetler", "potential_hook_territories", "primary_facts", "visual_moments"):
            deger = state.get(anahtar)
            if isinstance(deger, list):
                kaynaklar.extend(str(x) for x in deger if str(x or "").strip())
            elif isinstance(deger, str) and deger.strip():
                kaynaklar.append(deger)
    return kaynaklar


def _parcalar(metin: str) -> list:
    metin = _temizle(metin)
    return [p.strip(" ,;:") for p in _CUMLE_AYRAC_RE.split(metin) if p and p.strip()]


def _kaynaktan_alt(metin: str) -> str:
    """Kanıt cümlesinden 4-7 kelimelik, cümle düzeninde alt başlık türetir.
    Yalnızca DOĞAL parça (bütün cümle / yan cümle) kabul edilir. Türkçe yüklem
    sonda olduğu için uzun cümleyi 7. kelimede kırpmak yüklemi keser
    ("... fiziksel tuşlardan", "... ve 'teknolojik"); böyle yarım cümle yerine
    kurala uygun şablon alt başlıklara düşülür."""
    for parca in _parcalar(metin)[:3]:
        if ALT_MIN_KELIME <= kelime_sayisi(parca) <= ALT_MAX_KELIME:
            aday = alt_baslik_duzenle(parca)
            if _TEK_TIRNAK_RE.search(aday):
                aday = _cumle_duzeni(_kirpik_sonu_temizle(aday))
            if kelime_sayisi(aday) >= ALT_MIN_KELIME:
                return aday
    return ""


def _kaynaktan_ust(metin: str) -> str:
    """Yalnızca 2-4 kelimelik BÜTÜN ifadeler ÜST başlık olur (cümle ortasından kırpma yok)."""
    tam = _temizle(metin).rstrip(".!?…")
    for parca in [tam] + _parcalar(tam)[:2]:
        if UST_MIN_KELIME <= kelime_sayisi(parca) <= UST_MAX_KELIME:
            return ust_baslik_duzenle(parca)
    return ""


def kapak_basliklarini_normalize_et(ham, editorial_state=None, detective_state=None, hook_state=None, log=None) -> list:
    """LLM çıktısından kurala TAM uyan 5 alternatif üretir.

    Dönüş: tam 5 öğe; her biri {"ust", "alt", "ana", "kaynak"}.
    `ana` anahtarı (== ust) Telegram worker ve `_secilen_hook_getir` ile geriye
    dönük uyumluluk içindir.
    """
    sonuc, gorulen = [], set()

    def _ekle(ust, alt, kaynak):
        ust = ust_baslik_duzenle(ust)
        alt = alt_baslik_duzenle(alt)
        if not ust_kurala_uygun_mu(ust) or not alt_kurala_uygun_mu(alt):
            return False
        k_ust, k_alt = _anahtar(ust), _anahtar(alt)
        # Tekrar yasağı: aynı üst, aynı alt, üst==alt veya altın üstü aynen sürdürmesi.
        if k_ust in gorulen or k_alt in gorulen or k_ust == k_alt or k_alt.startswith(k_ust):
            return False
        gorulen.update({k_ust, k_alt})
        sonuc.append({"ust": ust, "alt": alt, "ana": ust, "kaynak": kaynak})
        return True

    kaynaklar = _kaynak_metinleri(editorial_state, detective_state, hook_state)
    alt_havuzu, ust_havuzu = [], []
    for k in kaynaklar:
        a, u = _kaynaktan_alt(k), _kaynaktan_ust(k)
        if a and _anahtar(a) not in {_anahtar(x) for x in alt_havuzu}:
            alt_havuzu.append(a)
        if u and _anahtar(u) not in {_anahtar(x) for x in ust_havuzu}:
            ust_havuzu.append(u)
    alt_havuzu += list(_YEDEK_ALT)
    ust_havuzu += list(_YEDEK_UST)

    def _bos_alt(ust_metni):
        k_ust = _anahtar(ust_metni)
        for aday in alt_havuzu:
            k = _anahtar(aday)
            if k in gorulen or k == k_ust or (k_ust and k.startswith(k_ust)):
                continue
            return aday
        return ""

    def _bos_ust(alt_metni):
        k_alt = _anahtar(alt_metni)
        for aday in ust_havuzu:
            k = _anahtar(aday)
            if k in gorulen or k == k_alt or k_alt.startswith(k):
                continue
            return aday
        return ""

    # 1) Ajan çıktısı: kurala uyanlar korunur, uymayanlar düzeltilir.
    for ust, alt in _aday_ciftler(ham):
        if len(sonuc) >= ALTERNATIF_SAYISI:
            break
        if not alt:
            n = kelime_sayisi(ust)
            if n > UST_MAX_KELIME:
                if n > ALT_MAX_KELIME:
                    continue  # 8+ kelimelik tek başlık: iki katmana bölünemez
                # 5-7 kelimelik tek başlık: bütün ifade ALT olur, ÜST kanıt havuzundan gelir.
                ust, alt = _bos_ust(ust), ust
            else:
                alt = _bos_alt(ust)
        _ekle(ust, alt, "agent")
    ajan_adedi = len(sonuc)

    # 2) Kanıt tabanlı yerel alternatifler (ÜST 2-4 kelimelik bütün ifade + farklı kanıttan ALT).
    for ust in ust_havuzu:
        if len(sonuc) >= ALTERNATIF_SAYISI:
            break
        if _anahtar(ust) in gorulen:
            continue
        _ekle(ust, _bos_alt(ust), "local" if ust not in _YEDEK_UST else "template")

    if callable(log):
        yerel = len(sonuc) - ajan_adedi
        if ajan_adedi >= ALTERNATIF_SAYISI:
            log(f"🎯 Kapak başlıkları: Hook ajanı {ajan_adedi} geçerli Üst/Alt alternatifi verdi.")
        else:
            log(f"⚠️ Kapak başlıkları: Hook ajanı {ajan_adedi} geçerli alternatif verdi; {yerel} alternatif Editorial/Detective verisinden yerel olarak tamamlandı (kural: 5 Üst/Alt).")
    return sonuc[:ALTERNATIF_SAYISI]


def kapak_basliklarini_metne_dok(basliklar, baslik_satiri="") -> str:
    """Kullanıcı çıktı şablonu:
        Alternatif N:
        Üst: [2-4 KELİME BÜYÜK HARF]
        Alt: [cümle düzeninde açıklayıcı metin]
    """
    satirlar = [baslik_satiri, ""] if baslik_satiri else []
    for i, item in enumerate(basliklar or [], 1):
        if isinstance(item, dict):
            ust = str(item.get("ust") or item.get("ana") or "").strip()
            alt = str(item.get("alt") or "").strip()
        else:
            ust, alt = str(item or "").strip(), ""
        if not ust and not alt:
            continue
        satirlar += [f"Alternatif {i}:", f"Üst: {ust}", f"Alt: {alt}", ""]
    return "\n".join(satirlar).strip()
