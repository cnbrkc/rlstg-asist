"""Agentic Web Search Katmanı.

DuckDuckGo ile çoklu sorgu yapar, sonuçları çapraz analiz için LLM'e hazır
hale getirir. API key gerektirmez, tamamen ücretsiz.
"""
import re
from typing import List, Dict, Any

def _temizle_metin(text: str, max_chars: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:max_chars] if len(text) > max_chars else text

def duckduckgo_sorgu(sorgu: str, max_sonuc: int = 5, log_ekle=None) -> List[Dict[str, str]]:
    """Tek bir DuckDuckGo sorgusu çalıştırır."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(sorgu, max_results=max_sonuc))
        temiz = []
        for r in results:
            title = _temizle_metin(r.get("title", ""), 150)
            body = _temizle_metin(r.get("body", ""), 500)
            href = str(r.get("href", "")).strip()
            if title and body:
                temiz.append({"baslik": title, "icerik": body, "kaynak": href})
        if log_ekle:
            log_ekle(f"🔍 DuckDuckGo '{sorgu[:60]}...' → {len(temiz)} sonuç")
        return temiz
    except Exception as e:
        if log_ekle:
            log_ekle(f"⚠️ DuckDuckGo sorgu hatası: {str(e)[:100]}")
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

    # 1. Kimlik ve Teknik
    sorgular.append(f"{tam_ad} özellikleri teknik 2025")
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
