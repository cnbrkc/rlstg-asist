"""Agentic Web Search Katmanı.

DuckDuckGo ile çoklu sorgu yapar, sonuçları çapraz analiz için LLM'e hazır
hale getirir. API key gerektirmez, tamamen ücretsiz.

Drift eden bir DDGS isteğinin susup pipepeline'ı takmasını önlemek için her
sorgu önce bellek içi (iş parçacığı + kuyruk) zaman aşımıyla denenir; bu ölçüm
"hâlâ çalışıyor" derse yanıt proses izolasyonuyla tekrar denenir ve eninde
sonunda zaman aşımına uğrar (boş sonuç döner, Research Fact Lock fallback'ine
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


def _env_int(ad: str, varsayilan: int) -> int:
    try:
        return max(1, int(os.environ.get(ad, varsayilan)))
    except (TypeError, ValueError):
        return varsayilan


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


def _raw_sonuclar(sorgu: str, max_sonuc: int, ddgs_cls) -> list:
    with ddgs_cls() as ddgs:
        return list(ddgs.text(sorgu, max_results=max_sonuc) or [])


def _run_calisan_ile(sorgu: str, max_sonuc: int, timeout: int, ddgs_cls):
    """Aynı proseste yan iş parçacığında DDGS çağrısı; yanıt kuyruktan okunur."""
    try:
        from queue import Queue
    except Exception:
        return None
    kuyruk = Queue()

    def calis():
        try:
            kuyruk.put(("ok", _raw_sonuclar(sorgu, max_sonuc, ddgs_cls)))
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


def _proses_hedefi(kuyruk, sorgu: str, max_sonuc: int) -> None:
    try:
        kuyruk.put(_raw_sonuclar(sorgu, max_sonuc, _ddgs_sinifi()))
    except Exception as e:  # noqa: BLE001
        kuyruk.put(e)


def _run_proses_ile(sorgu: str, max_sonuc: int, timeout: int):
    """DDGS yan iş parçacığında asılı kalırsa ayrı proses + kuyruk ile dener.
    Zaman aşımı / hata → None (boş sonuçtan AYIRT edilir; üst katman tekrar dener)."""
    try:
        ctx = multiprocessing.get_context("spawn")
        kuyruk = ctx.Queue()
        proc = ctx.Process(target=_proses_hedefi, args=(kuyruk, sorgu, max_sonuc))
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


def duckduckgo_sorgu(sorgu: str, max_sonuc: int = 5, log_ekle=None, hata_bildir=None) -> List[Dict[str, str]]:
    """Tek bir web sorgusu çalıştırır (ddgs metasearch).

    Drift eden bir isteğin susup kalmaması için önce iş parçacığı zaman aşımı,
    desteklenmezse proses izolasyonu denenir; iki katman da sessizce boş döner.
    hata_bildir: verilirse sorgu HATA/zaman aşımı/hız sınırı yüzünden sonuçsuz
    kaldığında çağrılır (gerçekten 0 sonuçlu sorgudan ayırt etmek için).
    """
    try:
        DDGS = _ddgs_sinifi()
        timeout = _env_int("DDGS_TIMEOUT", 8)
        ham = _run_calisan_ile(sorgu, max_sonuc, timeout, DDGS)
        if ham is None:
            ham = _run_proses_ile(sorgu, max_sonuc, timeout)
        if ham is None:
            if callable(hata_bildir):
                hata_bildir()
            if log_ekle:
                log_ekle(f"⚠️ Web '{sorgu[:60]}...' zaman aşımı/hata; sonuç alınamadı.")
            return []
        temiz = []
        for r in ham or []:
            title = _temizle_metin(r.get("title", ""), 150)
            body = _temizle_metin(r.get("body", ""), 800)
            href = str(r.get("href", "")).strip()
            if title and body:
                temiz.append({"baslik": title, "icerik": body, "kaynak": href})
        if log_ekle:
            log_ekle(f"🔍 Web '{sorgu[:60]}...' → {len(temiz)} sonuç")
        return temiz
    except Exception as e:
        if callable(hata_bildir):
            hata_bildir()
        if log_ekle:
            log_ekle(f"⚠️ Web sorgu hatası ({sorgu[:40]}...): {str(e)[:100]}")
        return []


def arastirma_sorgulari_olustur(video_state: Dict[str, Any]) -> List[str]:
    """Forensic analizden agentic sorgu listesi üretir."""
    sorgular = []
    kimlik = video_state.get("video_identity") or {}
    marka = str(kimlik.get("brand") or "").strip()
    model = str(kimlik.get("exact_model") or "").strip()
    variant = str(kimlik.get("variant") or "").strip()
    tam_ad = f"{marka} {model} {variant}".strip()

    if not marka or marka.upper() == "UNKNOWN":
        return []

    # 1. Kimlik ve Teknik (yıl: sorgu her zaman güncel kalır)
    yil = datetime.now(ZoneInfo("Europe/Istanbul")).year
    sorgular.append(f"{tam_ad} özellikleri teknik {yil}")
    # ÖTV dilimi motor hacmi + elektrik motoru kW olmadan seçilemez. Genel
    # "ÖTV" araması tablonun bütün satırlarını (%70/%170/%220) döndürür.
    sorgular.append(f"{tam_ad} motor silindir hacmi cc elektrik motor gücü kW")
    # 2. Çelişki ve Şikayet (Agentic fark yaratan sorgu)
    sorgular.append(f"{tam_ad} kullanıcı şikayet sorun gizli kusur")
    # 3. Türkiye Pazarı — modele özel oran, genel tablo değil
    sorgular.append(f"{tam_ad} Türkiye ÖTV oranı vergi dilimi {yil}")
    sorgular.append(f"{tam_ad} Türkiye fiyat satış")
    # 4. Viral Araştırma İhtiyaçları (Forensic'ten gelen dinamik sorular)
    for soru in (video_state.get("viral_arastirma_ihtiyaclari") or [])[:2]:
        soru_metni = _temizle_metin(soru, 120)
        if soru_metni:
            sorgular.append(f"{tam_ad} {soru_metni}")

    return sorgular[:7]


def otv_ek_arastirma(video_state: Dict[str, Any], log_ekle) -> str:
    """İlk tur tek ÖTV oranı kilitleyemediyse modele özel teknik + vergi sorgusu.

    Genel tablo snippet'i 500-800 karakterde %70 ve %170'i bir arada gösterir;
    bu ikinci tur motor hacmi / kW veya modele yazılmış oranı arar.
    """
    kimlik = (video_state or {}).get("video_identity") or {}
    marka = str(kimlik.get("brand") or "").strip()
    model = str(kimlik.get("exact_model") or "").strip()
    if not marka or marka.upper() == "UNKNOWN" or not model or model.upper() == "UNKNOWN":
        return ""
    yil = datetime.now(ZoneInfo("Europe/Istanbul")).year
    sorgular = [
        f"\"{marka} {model}\" motor hacmi cc elektrik motor kW hibrit",
        f"\"{marka} {model}\" Türkiye ÖTV yüzde {yil}",
    ]
    bloklar = []
    for sorgu in sorgular:
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
