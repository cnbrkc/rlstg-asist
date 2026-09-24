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
    """DDGS yan iş parçacığında asılı kalırsa ayrı proses + kuyruk ile dener."""
    try:
        ctx = multiprocessing.get_context("spawn")
        kuyruk = ctx.Queue()
        proc = ctx.Process(target=_proses_hedefi, args=(kuyruk, sorgu, max_sonuc))
        proc.start()
        proc.join(timeout=timeout)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
            return []
        if kuyruk.empty():
            return []
        sonuc = kuyruk.get_nowait()
        if isinstance(sonuc, Exception):
            raise sonuc
        return sonuc or []
    except Exception:
        return []


def duckduckgo_sorgu(sorgu: str, max_sonuc: int = 5, log_ekle=None) -> List[Dict[str, str]]:
    """Tek bir web sorgusu çalıştırır (ddgs metasearch).

    Drift eden bir isteğin susup kalmaması için önce iş parçacığı zaman aşımı,
    desteklenmezse proses izolasyonu denenir; iki katman da sessizce boş döner.
    """
    try:
        DDGS = _ddgs_sinifi()
        timeout = _env_int("DDGS_TIMEOUT", 8)
        ham = _run_calisan_ile(sorgu, max_sonuc, timeout, DDGS)
        if ham is None:
            ham = _run_proses_ile(sorgu, max_sonuc, timeout)
        temiz = []
        for r in ham or []:
            title = _temizle_metin(r.get("title", ""), 150)
            body = _temizle_metin(r.get("body", ""), 500)
            href = str(r.get("href", "")).strip()
            if title and body:
                temiz.append({"baslik": title, "icerik": body, "kaynak": href})
        if log_ekle:
            log_ekle(f"🔍 Web '{sorgu[:60]}...' → {len(temiz)} sonuç")
        return temiz
    except Exception as e:
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
    # 2. Çelişki ve Şikayet (Agentic fark yaratan sorgu)
    sorgular.append(f"{tam_ad} kullanıcı şikayet sorun gizli kusur")
    # 3. Türkiye Pazarı
    sorgular.append(f"{tam_ad} Türkiye fiyat satış ÖTV")
    # 4. Viral Araştırma İhtiyaçları (Forensic'ten gelen dinamik sorular)
    for soru in (video_state.get("viral_arastirma_ihtiyaclari") or [])[:2]:
        soru_metni = _temizle_metin(soru, 120)
        if soru_metni:
            sorgular.append(f"{tam_ad} {soru_metni}")

    return sorgular[:6]


def web_arastirma_yap(video_state: Dict[str, Any], log_ekle) -> str:
    """Tüm sorguları çalıştırır, LLM'e hazır metin bloğu döndürür."""
    sorgular = arastirma_sorgulari_olustur(video_state)
    if not sorgular:
        log_ekle("🔍 Marka/model belirsiz; web araştırması atlandı.")
        return ""

    bloklar = []
    for sorgu in sorgular:
        sonuclar = duckduckgo_sorgu(sorgu, max_sonuc=4, log_ekle=log_ekle)
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
