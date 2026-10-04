"""Agentic Web Search Katmanı.

Çoklu sağlayıcıyla sorgu yapar, sonuçları çapraz analiz için LLM'e hazır hale
getirir. API key gerektirmez, tamamen ücretsiz.

Dayanıklılık katmanları (04.10.2026 logu: "No results found" üst üste geldi):
  1. ddgs "auto" + TR bölgesi (region=tr-tr; varsayılan us-en Türkçe sorguları
     boş döndürüyordu),
  2. ddgs arka uç rotasyonu (bing/mojeek/brave/google/yahoo/startpage) ve haber
     kategorisi,
  3. core.arama_yedekleri: DuckDuckGo HTML, Mojeek, Google News RSS, Wikipedia —
     ddgs tamamen düşse bile araştırma kör kalmaz.
  4. "No results found" ddgs'te İSTİSNA olarak gelir; bu bir ağ hatası değil
     gerçekten sonuçsuzluktur — yedek sağlayıcılara geçilir, sorgu hatası
     olarak bildirilmez (paralel aşamadaki gereksiz seri tekrarı tetiklenmez).

Drift eden bir DDGS isteğinin susup pipeline'ı takmasını önlemek için her sorgu
önce bellek içi (iş parçacığı + kuyruk) zaman aşımıyla denenir; bu ölçüm "hâlâ
çalışıyor" derse yanıt proses izolasyonuyla tekrar denenir ve eninde sonunda
zaman aşımına uğrar (boş sonuç döner, Research Fact Lock fallback'ine
bırakılır). GitHub Actions'ta iş parçacığı çöp toplama zamanlayıcısı 'import os'
akışı nedeniyle sınırlı olabildiği için sıfır deneme yerine 1 güvenli deneme
tutulur.
"""
import multiprocessing
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Dict, Any

from core.arama_yedekleri import bolge as _bolge, http_yedek_sorgu_detayli


def _env_int(ad: str, varsayilan: int) -> int:
    try:
        return max(1, int(os.environ.get(ad, varsayilan)))
    except (TypeError, ValueError):
        return varsayilan


# Forensic analiz şeması modelden emin olamadığında 'UNKNOWN' yazmasını
# istiyor. Bu yer tutucu metni sorguya sızınca DDGS sorguyu terim sayıp
# "No results found" dönüyordu (01.10.2026 logu: 'Kia Seltos UNKNOWN Türkiye
# fiyat satış' → 0 sonuç, '... şikayet ...' → 4 sonuç). Kimlik alanları ve
# dinamik araştırma soruları sorgu üretiminde ayıklanır.
_YER_TUTUCU_DEGERLER = {
    "", "unknown", "unkown", "bilinmiyor", "bilinmeyen", "belirsiz",
    "tanimsiz", "tanımsız", "none", "null", "n/a", "na", "-", "--", "?", "??",
    "yok", "x", "xx",
}
# Metin içi token ayıklamada 'yok' GERÇEK bir kelimedir ("Türkiye'de satışı yok
# mu?") — yalnızca açıkça yer tutucu olan token'lar atılır.
_TOKEN_YER_TUTUCULAR = {
    "unknown", "unkown", "bilinmiyor", "bilinmeyen", "belirsiz",
    "tanimsiz", "tanımsız", "none", "null", "n/a", "na", "-", "--", "?", "??",
}

# ddgs "No results found." istisnası gerçekten sonuçsuzluğu anlatır; ağ hatası
# ya da hız sınırı değildir. Bu ayrım yapılmadığı sürece boş sorgular "hata"
# sayılıp gereksiz seri tekrarı tetikliyordu.
_BOS_SONUC_ISARETI = "no results found"

# ddgs arka uçları: "auto" bazı sorgularda hiçbir ağa bağlanamıyor; sabit
# rotasyon listesi TR/Türkçe sorgularda daha kararlı. Birden fazla arka uç
# virgülle verilir; ddgs sırayla dener (max_results dolunca durur).
_YEDEK_BACKEND_LISTESI = os.environ.get(
    "DDGS_FALLBACK_BACKENDS",
    "bing,mojeek,brave,google,yahoo,startpage",
).strip()
# Tek bir arka uç yavaş/bozuk olduğunda hepsini birden beklememek için ikinci tur.
_YEDEK_BACKEND_LISTESI_2 = os.environ.get(
    "DDGS_FALLBACK_BACKENDS_2",
    "brave,mojeek,bing,yandex",
).strip()


_ASCII_FOLD = str.maketrans("\u00e7\u011f\u0131\u00f6\u015f\u00fc\u00e2\u00ee\u00fb\u00c7\u011e\u0130\u00d6\u015e\u00dc\u00c2\u00ce\u00db", "cgiosuaiuCGIOSUAIU")


def _fold_metin(metin) -> str:
    """Türkçe diakritikten bağımsız karşılaştırma için metni katlar."""
    return str(metin or "").casefold().translate(_ASCII_FOLD)


def _kimlik_parcasi(deger) -> str:
    """Kimlik alanını ayıklar; yer tutucuysa ('UNKNOWN'/'bilinmiyor') boş döner."""
    temiz = str(deger or "").strip().strip("\"'`").strip()
    if temiz.casefold() in _YER_TUTUCU_DEGERLER:
        return ""
    return temiz


def _yer_tutucu_tokenlari_at(metin: str) -> str:
    """Serbest metindeki başına sonuna gelmiş yer tutucu token'larını atar."""
    parcalar = []
    for parca in re.split(r"\s+", str(metin or "")):
        saf = parca.strip("\"'`.,;:!?()[]{}").casefold()
        if saf in _TOKEN_YER_TUTUCULAR:
            continue
        parcalar.append(parca)
    return " ".join(parcalar).strip(" ,;:–—-")


def _basitlestirilmis_sorgu(sorgu: str, kimlik: str) -> str:
    """Sonuçsuz kalan sorguyu kimlik + ilk anahtar kelimelerle kısaltır.

    Kimlik önekini çıkarıp kalan kısmın ilk 4 kelimesini korur; hedef aynı
    araç/model için daha az terimli, DDGS'in getirebildiği sorgu.
    """
    kalan = str(sorgu or "")
    if kimlik and kalan.lower().startswith(kimlik.lower()):
        kalan = kalan[len(kimlik):].strip()
    kelimeler = kalan.split()[:4]
    return _yer_tutucu_tokenlari_at(f"{kimlik} {' '.join(kelimeler)}")


def _kisa_sorgu(metin: str, azami_kelime: int = 8) -> str:
    """Uzun serbest metni kısa sorguya indirger (DDGS uzun sorguda sonuçsuz kalıyor)."""
    kelimeler = _yer_tutucu_tokenlari_at(str(metin or "")).split()
    return " ".join(kelimeler[:azami_kelime]).strip()


def _temizle_metin(text: str, max_chars: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:max_chars] if len(text) > max_chars else text


def _ddgs_sinifi():
    """`duckduckgo_search` paketi `ddgs` adıyla yeniden adlandırıldı; eski paket
    artık uyarı basıp 0 sonuç döndürüyor. Önce yeni paketi, yoksa eskisini kullan."""
    try:
        from ddgs import DDGS  # type: ignore
        return DDGS
    except ImportError:
        import warnings
        warnings.filterwarnings("ignore", message=".*renamed to `ddgs`.*")
        from duckduckgo_search import DDGS  # type: ignore
        return DDGS


def _bos_sonuc_mu(hata: Exception) -> bool:
    return _BOS_SONUC_ISARETI in str(hata or "").casefold()


def _raw_sonuclar(sorgu: str, max_sonuc: int, ddgs_cls, backend: str = "", kategori: str = "text") -> list:
    kwargs: Dict[str, Any] = {"max_results": max_sonuc, "region": _bolge()}
    if backend:
        kwargs["backend"] = backend
    with ddgs_cls() as ddgs:
        if kategori == "news" and callable(getattr(ddgs, "news", None)):
            islev = ddgs.news
        else:
            islev = ddgs.text
        return list(islev(sorgu, **kwargs) or [])


def _run_calisan_ile(sorgu: str, max_sonuc: int, timeout: int, ddgs_cls, backend: str = "", kategori: str = "text"):
    """Aynı proseste yan iş parçacığında DDGS çağrısı; yanıt kuyruktan okunur."""
    try:
        from queue import Queue
    except Exception:
        return None
    kuyruk = Queue()

    def calis():
        try:
            kuyruk.put(("ok", _raw_sonuclar(sorgu, max_sonuc, ddgs_cls, backend, kategori)))
        except Exception as e:  # noqa: BLE001
            kuyruk.put(("hata", e))

    t = threading.Thread(target=calis, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return None  # hâlâ çalışıyor -> üst katman proses izolasyonu deneyecek
    try:
        durum, deger = kuyruk.get_nowait()
    except Exception:
        return None
    if durum == "ok":
        return deger
    raise deger


def _proses_hedefi(kuyruk, sorgu: str, max_sonuc: int, backend: str = "", kategori: str = "text") -> None:
    try:
        kuyruk.put(_raw_sonuclar(sorgu, max_sonuc, _ddgs_sinifi(), backend, kategori))
    except Exception as e:  # noqa: BLE001
        kuyruk.put(e)


def _run_proses_ile(sorgu: str, max_sonuc: int, timeout: int, backend: str = "", kategori: str = "text"):
    """DDGS yan iş parçacığında asılı kalırsa ayrı proses + kuyruk ile dener.
    Zaman aşımı / hata → None (boş sonuçtan AYIRT edilir; üst katman tekrar dener)."""
    try:
        ctx = multiprocessing.get_context("spawn")
        kuyruk = ctx.Queue()
        proc = ctx.Process(target=_proses_hedefi, args=(kuyruk, sorgu, max_sonuc, backend, kategori))
        proc.start()
        proc.join(timeout=timeout)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
            return None
        if kuyruk.empty():
            return None
        sonuc = kuyruk.get_nowait()
        if isinstance(sonuc, Exception):
            return None
        return sonuc or []
    except Exception:
        return None


def _ddgs_ham(
    sorgu: str,
    max_sonuc: int,
    backend: str = "",
    kategori: str = "text",
    timeout: int | None = None,
    proses_izolasyon: bool = True,
):
    """Tek ddgs turu. Dönüş: (sonuçlar, hata_var_mi, bos_mu).

    Yedek turlarda proses izolasyonu kapatılır (her tur için yeni süreç açmak
    sorgu bütçesini yiyor); asılı kalan iş parçacığı daemon olduğu için süreç
    sonunda ölür.
    """
    DDGS = _ddgs_sinifi()
    timeout = timeout or _env_int("DDGS_TIMEOUT", 8)
    try:
        ham = _run_calisan_ile(sorgu, max_sonuc, timeout, DDGS, backend, kategori)
        if ham is None and proses_izolasyon:
            ham = _run_proses_ile(sorgu, max_sonuc, timeout, backend, kategori)
    except Exception as exc:  # noqa: BLE001
        if _bos_sonuc_mu(exc):
            return [], False, True
        return [], True, False
    if ham is None:
        return [], True, False
    return list(ham or []), False, False


def _hazir_sonuclar(ham) -> List[Dict[str, str]]:
    """Yedek sağlayıcılardan HAZIR gelen (baslik/icerik/kaynak) sonuçları normalize eder.

    HTTP yedekleri `_temiz_sonuclar`'dan geçirilirse (o ddgs alan adlarını
    arar: title/body/href) tüm sonuçlar sessizce düşer — 04.10.2026'da yedek
    katmanın fiilen devre dışı kalmasına yol açıyordu.
    """
    temiz = []
    for r in ham or []:
        baslik = _temizle_metin(r.get("baslik", ""), 150)
        icerik = _temizle_metin(r.get("icerik", ""), 800)
        kaynak = str(r.get("kaynak", "") or "").strip()
        if baslik and icerik:
            temiz.append({"baslik": baslik, "icerik": icerik, "kaynak": kaynak})
    return temiz


def _temiz_sonuclar(ham) -> List[Dict[str, str]]:
    temiz = []
    for r in ham or []:
        title = _temizle_metin(r.get("title", ""), 150)
        body = _temizle_metin(r.get("body", "") or r.get("excerpt", ""), 800)
        href = str(r.get("href", "") or r.get("url", "")).strip()
        if title and body:
            temiz.append({"baslik": title, "icerik": body, "kaynak": href})
    return temiz


def _ddgs_yedek_turu(sorgu: str, max_sonuc: int, log_ekle, backend: str, kategori: str, etiket: str, timeout: int = 6):
    """Sabit arka uç listesiyle ddgs turu; (sonuçlar, hata_var_mi) döner."""
    if not backend:
        return [], False
    ham, hata, bos = _ddgs_ham(
        sorgu, max_sonuc, backend=backend, kategori=kategori,
        timeout=timeout, proses_izolasyon=False,
    )
    temiz = _temiz_sonuclar(ham)
    if temiz and callable(log_ekle):
        log_ekle(f"🔁 Web '{sorgu[:60]}...' → {len(temiz)} sonuç (yedek: {etiket})")
    return temiz, (hata and not temiz and not bos)


def duckduckgo_sorgu(sorgu: str, max_sonuc: int = 5, log_ekle=None, hata_bildir=None) -> List[Dict[str, str]]:
    """Tek bir web sorgusu çalıştırır (çoklu sağlayıcı, çok katmanlı yedekli).

    Sıra: ddgs auto (TR) → ddgs arka uç rotasyonu → ddgs haber → HTTP yedekleri.
    Drift eden bir isteğin susup kalmaması için iş parçacığı + proses zaman
    aşımı kullanılır; tüm katmanlar sessizce boş döner. Toplam süre
    WEB_SEARCH_QUERY_BUDGET (varsayılan 30 sn) ile sınırlıdır. hata_bildir:
    sorgu HATA/zaman aşımı/hız sınırı yüzünden sonuçsuz kaldığında çağrılır
    (gerçekten 0 sonuçlu sorgudan ayırt etmek için); "No results found" bir
    hata DEĞİLDİR ve yedek sağlayıcılara geçilir.
    """
    bitis = time.monotonic() + _env_int("WEB_SEARCH_QUERY_BUDGET", 30)
    try:
        ham, birincil_hata, _ = _ddgs_ham(sorgu, max_sonuc)
        temiz = _temiz_sonuclar(ham)
        if temiz:
            if log_ekle:
                log_ekle(f"🔍 Web '{sorgu[:60]}...' → {len(temiz)} sonuç")
            return temiz

        yedek_hatalar = []
        if time.monotonic() < bitis:
            temiz, hata = _ddgs_yedek_turu(
                sorgu, max_sonuc, log_ekle, _YEDEK_BACKEND_LISTESI, "text", "arka uç rotasyonu",
            )
            if temiz:
                return temiz
            yedek_hatalar.append(hata)

        if _YEDEK_BACKEND_LISTESI_2 and time.monotonic() < bitis:
            temiz, hata = _ddgs_yedek_turu(
                sorgu, max_sonuc, log_ekle, _YEDEK_BACKEND_LISTESI_2, "news", "haber rotasyonu",
            )
            if temiz:
                return temiz
            yedek_hatalar.append(hata)

        kalan = max(1, int(bitis - time.monotonic()))
        temiz, http_hatalar = http_yedek_sorgu_detayli(sorgu, max_sonuc, log_ekle, butce_saniye=kalan)
        temiz = _hazir_sonuclar(temiz)
        if temiz:
            return temiz

        if callable(log_ekle):
            log_ekle(f"ℹ️ Web '{sorgu[:50]}...' tüm sağlayıcılarda sonuçsuz (hata değil).")
        if birincil_hata or any(yedek_hatalar) or http_hatalar:
            if callable(hata_bildir):
                hata_bildir()
            if callable(log_ekle):
                log_ekle(f"⚠️ Web '{sorgu[:60]}...' zaman aşımı/hata; sonuç alınamadı.")
        return []
    except Exception as e:  # noqa: BLE001
        if callable(hata_bildir):
            hata_bildir()
        if callable(log_ekle):
            log_ekle(f"⚠️ Web sorgu hatası ({sorgu[:40]}...): {str(e)[:100]}")
        return []


def arastirma_sorgulari_olustur(video_state: Dict[str, Any]) -> List[str]:
    """Forensic analizden agentic sorgu listesi üretir.

    Sorgular KISA tutulur: ddgs uzun/terim ağırlıklı sorgularda "No results
    found" dönüyor (04.10.2026: 'Kia Seltos motor silindir hacmi cc elektrik
    motor gücü kW' → 0 sonuç; 'Kia Seltos motor silindir hacmi cc' → 4 sonuç).
    """
    sorgular = []
    kimlik = video_state.get("video_identity") or {}
    marka = _kimlik_parcasi(kimlik.get("brand"))
    model = _kimlik_parcasi(kimlik.get("exact_model"))
    variant = _kimlik_parcasi(kimlik.get("variant"))
    # 'UNKNOWN' variant/model sorgu içinde kalırsa DDGS terim sayıp sonuçsuz
    # dönüyor; yer tutucular ayıklanır (bkz. _YER_TUTUCU_DEGERLER).
    tam_ad = _yer_tutucu_tokenlari_at(f"{marka} {model} {variant}")

    if not marka:
        return []

    # 1. Kimlik ve Teknik (yıl: sorgu her zaman güncel kalır)
    yil = datetime.now(ZoneInfo("Europe/Istanbul")).year
    sorgular.append(f"{tam_ad} özellikleri teknik {yil}")
    # ÖTV dilimi motor hacmi + elektrik motoru kW olmadan seçilemez. Genel
    # "ÖTV" araması tablonun bütün satırlarını (%70/%170/%220) döndürür.
    # Uzun tek sorgu yerine iki KISA sorgu: ddgs terim yığınında sonuçsuz kalıyor.
    sorgular.append(f"{tam_ad} motor hacmi cc")
    sorgular.append(f"{tam_ad} elektrik motoru gücü kW")
    # 2. Çelişki ve Şikayet (Agentic fark yaratan sorgu)
    sorgular.append(f"{tam_ad} kullanıcı şikayet sorun gizli kusur")
    # 3. Türkiye Pazarı — modele özel oran, genel tablo değil
    sorgular.append(f"{tam_ad} Türkiye ÖTV oranı {yil}")
    sorgular.append(f"{tam_ad} Türkiye fiyat satış")
    # 4. Viral Araştırma İhtiyaçları (Forensic'ten gelen dinamik sorular)
    for soru in (video_state.get("viral_arastirma_ihtiyaclari") or [])[:2]:
        soru_metni = _kisa_sorgu(_temizle_metin(soru, 120), azami_kelime=6)
        if not soru_metni:
            continue
        if model and _fold_metin(model) in _fold_metin(soru_metni):
            # Soru zaten modeli anıyor: "Kia Seltos Türkiye'deki Kia Seltos..."
            # gibi model adı tekrarını önle.
            sorgular.append(_yer_tutucu_tokenlari_at(soru_metni))
        else:
            sorgular.append(_yer_tutucu_tokenlari_at(f"{tam_ad} {soru_metni}"))

    return sorgular[:8]


def otv_ek_arastirma(video_state: Dict[str, Any], log_ekle) -> str:
    """İlk tur tek ÖTV oranı kilitleyemediyse modele özel teknik + vergi sorgusu.

    Genel tablo snippet'i 500-800 karakterde %70 ve %170'i bir arada gösterir;
    bu ikinci tur motor hacmi / kW veya modele yazılmış oranı arar.

    Sorgular KISA ve sıralı denenir; iki bloğa ulaşınca durur (süre bütçesi).
    Tırnak içine alınmış sorgular DDGS'de tam ifade aramasına dönüşüp
    "No results found" veriyordu (01.10.2026 logu); sorgular düz metin kurulur,
    model bilinmiyorsa marka ile devam edilir.
    """
    kimlik = (video_state or {}).get("video_identity") or {}
    marka = _kimlik_parcasi(kimlik.get("brand"))
    model = _kimlik_parcasi(kimlik.get("exact_model"))
    if not marka:
        return ""
    kimlik_metni = _yer_tutucu_tokenlari_at(f"{marka} {model}")
    yil = datetime.now(ZoneInfo("Europe/Istanbul")).year
    adaylar = [
        f"{kimlik_metni} motor hacmi cc",
        f"{kimlik_metni} elektrik motoru kW",
        f"{kimlik_metni} ÖTV oranı",
        f"{kimlik_metni} Türkiye ÖTV {yil}",
    ]
    bloklar = []
    for sorgu in adaylar:
        if len(bloklar) >= 2:
            break
        sonuclar = duckduckgo_sorgu(sorgu, max_sonuc=4, log_ekle=log_ekle)
        if not sonuclar:
            continue
        satirlar = [f"SORGU: {sorgu}"]
        for i, s in enumerate(sonuclar, 1):
            satirlar.append(f"  [{i}] {s['baslik']}")
            satirlar.append(f"      {s['icerik']}")
        bloklar.append("\n".join(satirlar))
    if bloklar and callable(log_ekle):
        log_ekle(f"🔒 ÖTV kilidi için ek teknik/vergi araması: {len(bloklar)} sorgu.")
    return "\n\n".join(bloklar)


def web_arastirma_yap(video_state: Dict[str, Any], log_ekle) -> str:
    """Tüm sorguları çalıştırır, LLM'e hazır metin bloğu döndürür."""
    sorgular = arastirma_sorgulari_olustur(video_state)
    if not sorgular:
        log_ekle("🔍 Marka/model belirsiz; web araştırması atlandı.")
        return ""

    # Sorgular birbirinden bağımsız: seri çalıştırıldığında 5 sorgu ~11 sn
    # sürüyordu. Küçük bir havuzla eşzamanlı çalıştırılır (DDGS rate-limit'e
    # takılmamak için en fazla WEB_SEARCH_PARALLEL iş parçacığı); sonuç sırası
    # sorgu sırasıyla aynı kalır.
    paralel = min(len(sorgular), _env_int("WEB_SEARCH_PARALLEL", 3))
    if paralel > 1:
        hatali = set()
        kilit = threading.Lock()

        def _sorgula(indeks_sorgu):
            indeks, q = indeks_sorgu

            def _bildir():
                with kilit:
                    hatali.add(indeks)

            return duckduckgo_sorgu(q, max_sonuc=4, log_ekle=log_ekle, hata_bildir=_bildir)

        with ThreadPoolExecutor(max_workers=paralel, thread_name_prefix="ddgs") as havuz:
            tum_sonuclar = list(havuz.map(_sorgula, enumerate(sorgular)))
        # KALİTE: Paralel aşamada hata/hız sınırı/zaman aşımı yüzünden sonuçsuz
        # kalan sorgular kısa bir aradan sonra SERİ olarak bir kez daha denenir;
        # paralellik araştırma kapsamını asla daraltmaz.
        tekrar = sorted(i for i in hatali if not tum_sonuclar[i])
        if tekrar:
            log_ekle(f"🔁 {len(tekrar)} web sorgusu paralel aşamada hata/hız sınırı aldı; seri olarak yeniden deneniyor.")
            time.sleep(_env_int("WEB_SEARCH_RETRY_DELAY", 2))
            for i in tekrar:
                tum_sonuclar[i] = duckduckgo_sorgu(sorgular[i], max_sonuc=4, log_ekle=log_ekle)
    else:
        tum_sonuclar = [duckduckgo_sorgu(q, max_sonuc=4, log_ekle=log_ekle) for q in sorgular]

    # KALİTE: Hâlâ sonuçsuz kalan sorgular bir kez BASİTLEŞTİRİLİP yeniden
    # denenir. Uzun/terim ağırlıklı sorgu DDGS'de "No results found" dönebiliyor
    # diye (01.10.2026: 'Kia Seltos ... Türkiye fiyat satış' → 0 sonuç) aynı
    # araç için kısa savunma hattı: kimlik + ilk anahtar kelimeler.
    kimlik = _yer_tutucu_tokenlari_at(" ".join(
        _kimlik_parcasi((video_state.get("video_identity") or {}).get(alan))
        for alan in ("brand", "exact_model")
    ))
    for i, sonuc in enumerate(tum_sonuclar):
        if sonuc:
            continue
        basit = _basitlestirilmis_sorgu(sorgular[i], kimlik)
        if not basit or basit == sorgular[i]:
            continue
        log_ekle(f"🔁 Sonuçsuz sorgu basitleştirilip tekrar deneniyor: '{basit[:60]}'")
        tum_sonuclar[i] = duckduckgo_sorgu(basit, max_sonuc=4, log_ekle=log_ekle)

    bloklar = []
    for sorgu, sonuclar in zip(sorgular, tum_sonuclar):
        if sonuclar:
            satirlar = [f"SORGU: {sorgu}"]
            for i, s in enumerate(sonuclar, 1):
                satirlar.append(f"  [{i}] {s['baslik']}")
                satirlar.append(f"      {s['icerik']}")
            bloklar.append("\n".join(satirlar))

    if not bloklar:
        log_ekle("⚠️ Web araştırması sonuç üretmedi.")
        return ""

    log_ekle(f"✅ Web araştırması tamamlandı: {len(bloklar)} sorgu bloğu.")
    return "\n\n---\n\n".join(bloklar)
