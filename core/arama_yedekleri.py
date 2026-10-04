"""ddgs dışı yedek arama sağlayıcıları (API anahtarı gerektirmez).

Kök neden (04.10.2026 üretim logu): ddgs "auto" arka uçları Türkçe ve çok
terimli sorgularda sonuç getirmedi:

    Kia Seltos motor silindir hacmi cc elektrik motor gücü kW → No results found
    Kia Seltos Türkiye ÖTV yüzde 2026                        → No results found
    Kia Seltos motor silindir hacmi cc                       → 4 sonuç

Aynı araç için arama daralınca ÖTV kilidi matrah/motor hacmi verisini
bulamıyor, Fact Lock fiyat alanı boş kalıyor ve içerik vergi dilimlerine
sapıyordu. Tek sağlayıcıya bağlı kalmak yerine bu modül bağımsız yedek
arama yolları sunar:

  1. ddgs arka uç rotasyonu ve haber kategorisi  → core.web_search içinde
  2. Mojeek HTML                                 → burada
  3. Bing RSS + DuckDuckGo HTML                  → burada
  4. Google News RSS (TR)                        → burada
  5. Wikipedia API (tr → en)                     → burada

Sağlayıcılar sırayla denenir; ilk sonuçlu olan kazanır. Hepsi başarısız olursa
boş liste döner ve web_search üst katmanı Fact Lock fallback'ine bırakır.
Bu modül asla istisna sızdırmaz: arama katmanı üretimi durdurmaz.
"""
import html as _html
import os
import re
import time
from typing import Any, Dict, List
from urllib.parse import parse_qs, unquote, urlparse

try:
    import requests
except Exception:  # pragma: no cover - requests zorunlu bağımlılık, savunma amaçlı
    requests = None

# Tarayıcı benzeri UA: DDG/Mojeek HTML uç noktaları bot UA'sına anomali sayfası
# döndürüyor; GitHub Actions IP'lerinde bu başlık olmadan sonuç gelmiyor.
_KULLANICI_AJANI = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_VARSAYILAN_BOLGE = "tr-tr"
_ETIKET = re.compile(r"<[^>]+>")
_BOSLUK = re.compile(r"\s+")


def _env_int(ad: str, varsayilan: int) -> int:
    try:
        return max(1, int(os.environ.get(ad, varsayilan)))
    except (TypeError, ValueError):
        return varsayilan


def bolge() -> str:
    """Arama bölgesi (DDGS 'region' biçimi: ülke-dil). Türkçe içerik için tr-tr."""
    ham = str(os.environ.get("DDGS_REGION") or _VARSAYILAN_BOLGE).strip().lower()
    return ham if re.fullmatch(r"[a-z]{2}-[a-z]{2}", ham) else _VARSAYILAN_BOLGE


def _zaman_asimi() -> int:
    return _env_int("WEB_SEARCH_YEDEK_TIMEOUT", 8)


def _butce() -> float:
    return float(_env_int("WEB_SEARCH_YEDEK_BUTCE", 24))


def _basliklar() -> Dict[str, str]:
    return {
        "User-Agent": _KULLANICI_AJANI,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.6",
    }


def _metne_cevir(parca: Any, max_chars: int = 800) -> str:
    """HTML parçasını düz metne indirger (etiketler ve kaçış dizileri temizlenir)."""
    metin = _html.unescape(_ETIKET.sub(" ", str(parca or "")))
    # CDATA sarmalayıcı artıkları (Google News RSS) metne sızmasın.
    metin = re.sub(r"<!\[CDATA\[|\]\]>", " ", metin)
    metin = _BOSLUK.sub(" ", metin).strip()
    return metin[:max_chars]


def _sonuc(baslik: str, icerik: str, kaynak: str) -> Dict[str, str]:
    return {
        "baslik": str(baslik or "").strip()[:200],
        "icerik": str(icerik or "").strip()[:800],
        "kaynak": str(kaynak or "").strip(),
    }


def _gecerli(sonuclar: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [s for s in (sonuclar or []) if s.get("baslik") and s.get("icerik")]


def _ilgisiz_mi(sorgu: str, sonuc: Dict[str, str]) -> bool:
    """Sorgudaki anlamlı bir kelime başlık/gövdede geçmiyorsa sonucu ele.

    Wikipedia gibi geniş kapsamlı sağlayıcılar her sorguya "bir şey" döndürür;
    alakasız madde araştırmayı kirletmesin. Diğer sağlayıcılara uygulanmaz.
    """
    kelimeler = [k for k in re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü0-9]{3,}", str(sorgu or ""))]
    if not kelimeler:
        return False
    havuz = f"{sonuc.get('baslik', '')} {sonuc.get('icerik', '')}".casefold()
    return not any(k.casefold() in havuz for k in kelimeler)


def _ddg_yonlendirme_hedefi(href: str) -> str:
    """DDG HTML sonuç bağlantısı '/l/?uddg=...' yönlendirmesi olabilir; hedefi çöz."""
    if not href:
        return ""
    try:
        if "uddg=" in href:
            hedef = parse_qs(urlparse(href).query).get("uddg", [""])[0]
            return unquote(hedef) or href
    except Exception:
        pass
    return href


# --- 1. DuckDuckGo HTML uç noktası ---------------------------------------------

def _ddg_html_sorgu(sorgu: str, max_sonuc: int) -> List[Dict[str, str]]:
    if requests is None:
        return []
    yanit = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": sorgu, "kl": bolge()},
        headers=_basliklar(),
        timeout=_zaman_asimi(),
    )
    if not getattr(yanit, "ok", False):
        return []
    govde = str(getattr(yanit, "text", "") or "")
    bloklar = list(re.finditer(
        r'<a\b[^>]*class="result__a"[^>]*>(?P<baslik>.*?)</a>', govde, re.S | re.I,
    ))
    sonuclar = []
    for i, blok in enumerate(bloklar):
        if len(sonuclar) >= max_sonuc:
            break
        etiket = blok.group(0)
        href = re.search(r'href="([^"]+)"', etiket, re.I)
        baslik = _metne_cevir(blok.group("baslik"), 200)
        if not baslik:
            continue
        son = bloklar[i + 1].start() if i + 1 < len(bloklar) else len(govde)
        kuyruk = govde[blok.end():son]
        ozet = re.search(
            r'class="result__snippet"[^>]*>(?P<ozet>.*?)</(?:a|div|td|span)>', kuyruk, re.S | re.I,
        )
        icerik = _metne_cevir(ozet.group("ozet") if ozet else "", 500)
        if not icerik:
            continue
        sonuclar.append(_sonuc(baslik, icerik, _ddg_yonlendirme_hedefi(href.group(1) if href else "")))
    return sonuclar


# --- 2. Mojeek ----------------------------------------------------------------

def _mojeek_sorgu(sorgu: str, max_sonuc: int) -> List[Dict[str, str]]:
    if requests is None:
        return []
    yanit = requests.get(
        "https://www.mojeek.com/search",
        params={"q": sorgu},
        headers=_basliklar(),
        timeout=_zaman_asimi(),
    )
    if not getattr(yanit, "ok", False):
        return []
    govde = str(getattr(yanit, "text", "") or "")
    bloklar = list(re.finditer(
        r'<a\b[^>]*class="ob"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<baslik>.*?)</a>', govde, re.S | re.I,
    ))
    sonuclar = []
    for i, blok in enumerate(bloklar):
        if len(sonuclar) >= max_sonuc:
            break
        baslik = _metne_cevir(blok.group("baslik"), 200)
        son = bloklar[i + 1].start() if i + 1 < len(bloklar) else len(govde)
        kuyruk = govde[blok.end():son]
        ozet = re.search(r'<p\b[^>]*class="s"[^>]*>(?P<ozet>.*?)</p>', kuyruk, re.S | re.I)
        icerik = _metne_cevir(ozet.group("ozet") if ozet else "", 500)
        if not baslik or not icerik:
            continue
        sonuclar.append(_sonuc(baslik, icerik, unquote(blok.group("href"))))
    return sonuclar


# --- 3. RSS tabanlı arama (Google News + Bing RSS) ----------------------------

def _rss_sonuclari(govde: str, max_sonuc: int) -> List[Dict[str, str]]:
    """RSS gövdesini (Google News / Bing) ortak biçimde ayrıştırır."""
    sonuclar = []
    for blok in re.findall(r"<item>(.*?)</item>", govde, re.S | re.I)[:max_sonuc]:
        blok = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", blok, flags=re.S)
        baslik_m = re.search(r"<title>(.*?)</title>", blok, re.S | re.I)
        link_m = re.search(r"<link>(.*?)</link>", blok, re.S | re.I)
        aciklama_m = re.search(r"<description>(.*?)</description>", blok, re.S | re.I)
        baslik = _metne_cevir(baslik_m.group(1) if baslik_m else "", 200)
        icerik = _metne_cevir(aciklama_m.group(1) if aciklama_m else "", 500)
        if not baslik:
            continue
        sonuclar.append(_sonuc(baslik, icerik or baslik, _metne_cevir(link_m.group(1) if link_m else "", 300)))
    return sonuclar


def _rss_sorgu(url: str, params: Dict[str, Any], max_sonuc: int) -> List[Dict[str, str]]:
    if requests is None:
        return []
    yanit = requests.get(url, params=params, headers=_basliklar(), timeout=_zaman_asimi())
    if not getattr(yanit, "ok", False):
        return []
    return _rss_sonuclari(str(getattr(yanit, "text", "") or ""), max_sonuc)


def _google_haber_sorgu(sorgu: str, max_sonuc: int) -> List[Dict[str, str]]:
    return _rss_sorgu(
        "https://news.google.com/rss/search",
        {"q": sorgu, "hl": "tr", "gl": "TR", "ceid": "TR:tr"},
        max_sonuc,
    )


def _bing_rss_sorgu(sorgu: str, max_sonuc: int) -> List[Dict[str, str]]:
    """Bing'in anahtarsız RSS çıktısı; Google News tek başına düştüğünde ağ.

    04.10.2026: sağlayıcı çeşitliliği tek noktaya bağımlılığı kaldırır.
    """
    return _rss_sorgu(
        "https://www.bing.com/search",
        {"q": sorgu, "format": "rss", "cc": "TR", "setlang": "tr"},
        max_sonuc,
    )


# --- 4. Wikipedia API ---------------------------------------------------------

def _wikipedia_sorgu(sorgu: str, max_sonuc: int) -> List[Dict[str, str]]:
    if requests is None:
        return []
    sonuclar = []
    for dil in ("tr", "en"):
        try:
            yanit = requests.get(
                f"https://{dil}.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": sorgu,
                    "srlimit": max(1, min(max_sonuc, 5)),
                    "format": "json",
                    "utf8": 1,
                },
                headers=_basliklar(),
                timeout=_zaman_asimi(),
            )
            if not getattr(yanit, "ok", False):
                continue
            maddeler = (((yanit.json() or {}).get("query") or {}).get("search") or [])
        except Exception:
            continue
        for madde in maddeler:
            baslik = _metne_cevir(madde.get("title"), 200)
            icerik = _metne_cevir(madde.get("snippet"), 500)
            if not baslik or not icerik:
                continue
            aday = _sonuc(baslik, icerik, f"https://{dil}.wikipedia.org/wiki/{baslik.replace(' ', '_')}")
            if _ilgisiz_mi(sorgu, aday):
                continue
            sonuclar.append(aday)
            if len(sonuclar) >= max_sonuc:
                return sonuclar
        if sonuclar:
            return sonuclar
    return sonuclar


# Sağlayıcı sırası: TR pazarı için en isabetli olandan en genele doğru.
# Sıra: birincil ddgs zaten DuckDuckGo altyapısını denediği için önce FARKLI
# ağlar (Mojeek, Bing RSS), sonra DDG HTML, en son dar kapsamlı Wikipedia.
_SAGLAYICILAR = (
    ("mojeek", _mojeek_sorgu),
    ("bing-rss", _bing_rss_sorgu),
    ("duckduckgo-html", _ddg_html_sorgu),
    ("google-news", _google_haber_sorgu),
    ("wikipedia", _wikipedia_sorgu),
)


def http_yedek_sorgu_detayli(sorgu: str, max_sonuc: int, log_ekle, butce_saniye=None):
    """(sonuçlar, hatalar) döner; hatalar tamamlanamayan sağlayıcıları listeler."""
    if requests is None or not str(sorgu or "").strip():
        return [], ["requests-yok"]
    try:
        butce = float(butce_saniye) if butce_saniye is not None else _butce()
    except (TypeError, ValueError):
        butce = _butce()
    butce = max(1.0, min(butce, _butce()))
    baslangic = time.monotonic()
    hatalar = []
    for ad, islev in _SAGLAYICILAR:
        if time.monotonic() - baslangic >= butce:
            hatalar.append("butce-doldu")
            break
        try:
            sonuclar = _gecerli(islev(sorgu, max_sonuc))
        except Exception as exc:  # noqa: BLE001 - yedek katman üretimi durdurmaz
            hatalar.append(f"{ad}:{type(exc).__name__}")
            continue
        if sonuclar:
            if callable(log_ekle):
                log_ekle(f"🔁 Web '{sorgu[:60]}...' → {len(sonuclar)} sonuç (yedek kaynak: {ad})")
            return sonuclar, hatalar
    if callable(log_ekle) and hatalar:
        log_ekle(f"⚠️ Web '{sorgu[:40]}...' yedek sağlayıcılarında hata: {', '.join(str(h)[:40] for h in hatalar[:3])}")
    return [], hatalar


def http_yedek_sorgu(sorgu: str, max_sonuc: int = 4, log_ekle=None, butce_saniye=None) -> List[Dict[str, str]]:
    """ddgs dışı sağlayıcılarla arama yapar; ilk sonuçlu sağlayıcıyı döndürür.

    Toplam süre WEB_SEARCH_YEDEK_BUTCE saniyesiyle (veya verilen butce_saniye)
    sınırlıdır: yavaş sağlayıcı kalan denemeleri bütçesiz uzatamaz. Tüm hatalar
    yutulur (boş liste döner).
    """
    return http_yedek_sorgu_detayli(sorgu, max_sonuc, log_ekle, butce_saniye)[0]
