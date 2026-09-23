"""Telegram pipeline runtime guards.

Production guard: Agentic content generation + multi-speaker TTS request.
Social outputs are validated so filesystem/audio artifacts can never be sent as
Instagram/Facebook captions or Threads text.
"""

import os
import sys

# Add repository root to search path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pipeline as _pipeline
from core.social_fallbacks import (
    caption_fallback, looks_like_artifact, sanitize_hashtags,
    text as _text, threads_fallback,
)

SOCIAL_REGEN_MAX = 1

_original_caption = _pipeline._caption_calistir
_original_threads = _pipeline._threads_calistir


def _caption_guard(router, reels_state, fact_state, editorial_state, video_state, log, ton=None):
    for attempt in range(SOCIAL_REGEN_MAX + 1):
        try:
            state, model = _original_caption(router, reels_state, fact_state, editorial_state, video_state, log, ton)
        except Exception as exc:
            log(f"⚠️ Caption üretimi hata verdi: {str(exc)[:160]}")
            state, model = {"reels_aciklamasi": "", "reels_hashtagleri": []}, "hata"
        description = _text((state or {}).get("reels_aciklamasi"))
        hashtags = sanitize_hashtags((state or {}).get("reels_hashtagleri"))
        if description and not looks_like_artifact(description) and hashtags:
            return {"reels_aciklamasi": description, "reels_hashtagleri": hashtags}, model
        if attempt < SOCIAL_REGEN_MAX:
            reason = "artifact/boş" if looks_like_artifact(description) else "eksik"
            log(f"⚠️ Caption {reason} döndü; tek kontrollü yeniden üretim ({attempt + 1}/{SOCIAL_REGEN_MAX}).")
    description, hashtags = caption_fallback(reels_state, fact_state, editorial_state, video_state)
    log("⚠️ Caption modeli geçerli sosyal metin vermedi; Fact Lock tabanlı güvenli fallback kullanıldı.")
    return {"reels_aciklamasi": description, "reels_hashtagleri": hashtags}, "local-fallback"


def _threads_guard(router, video_state, fact_state, editorial_state, log, ton=None):
    for attempt in range(SOCIAL_REGEN_MAX + 1):
        try:
            state, model = _original_threads(router, video_state, fact_state, editorial_state, log, ton)
        except Exception as exc:
            log(f"⚠️ Threads üretimi hata verdi: {str(exc)[:160]}")
            state, model = {"threads_aciklamasi": ""}, "hata"
        text_value = _text((state or {}).get("threads_aciklamasi"))
        if text_value and not looks_like_artifact(text_value):
            return {"threads_aciklamasi": text_value}, model
        if attempt < SOCIAL_REGEN_MAX:
            reason = "artifact/boş" if looks_like_artifact(text_value) else "geçersiz"
            log(f"⚠️ Threads {reason} döndü; tek kontrollü yeniden üretim ({attempt + 1}/{SOCIAL_REGEN_MAX}).")
    text_value = threads_fallback(fact_state, editorial_state, video_state)
    log("⚠️ Threads modeli geçerli sosyal metin vermedi; Fact Lock tabanlı güvenli fallback kullanıldı.")
    return {"threads_aciklamasi": text_value}, "local-fallback"


def _single_pass_reels_and_tts(router, editorial_state, fact_state, video_state, notes, sure_saniye, ton, legacy_voice, log, baslangic_talimati="", ses_modu_notlari=None):
    """Agentic 4'lü ajan sistemi ile içerik üretimi + TTS."""
    mod_karari = _pipeline._mod_karari_al(editorial_state)
    
    reels_state, model_reels, duo_plan, duo_script, ses_ok, ses_model, ses_modu, ses_dosyasi, metadata_state = _pipeline.agentic_icerik_uretimi(
        router, video_state, fact_state, editorial_state, sure_saniye, ton, legacy_voice, log, mod_karari=mod_karari
    )
    
    return reels_state, model_reels, duo_plan, duo_script, ses_ok, ses_model, ses_modu, ses_dosyasi


_pipeline._caption_calistir = _caption_guard
_pipeline._threads_calistir = _threads_guard
