"""Telegram pipeline runtime guards.

Production guard: one Reels text generation + one multi-speaker TTS request.
Social outputs are validated so filesystem/audio artifacts can never be sent as
Instagram/Facebook captions or Threads text.
"""

import os
import re
import sys

# Add repository root to search path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pipeline as _pipeline
from core.media import gecici_ses_yolu, temp_dosya_temizle
from core.config import SES_HIZ_CARPANI, KELIME_HIZI_ORANI
from duo.duo_audio import duo_ses_uret
from core.social_fallbacks import (
    caption_fallback, looks_like_artifact, sanitize_hashtags,
    text as _text, threads_fallback,
)

SOCIAL_REGEN_MAX = 1

_original_caption = _pipeline._caption_calistir
_original_threads = _pipeline._threads_calistir
_original_reels_creative = _pipeline._reels_creative_calistir


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


def _split_for_speakers(text, conversation_map):
    """Split the single approved voiceover text locally; never rewrite it."""
    text = _text(text)
    turns = [x for x in (conversation_map or []) if isinstance(x, dict) and _text(x.get("speaker"))]
    if not text or not turns:
        return []
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    n = len(turns)
    if len(sentences) >= n:
        base, extra = divmod(len(sentences), n)
        chunks = []
        pos = 0
        for i in range(n):
            size = base + (1 if i < extra else 0)
            chunks.append(" ".join(sentences[pos:pos + size]).strip())
            pos += size
    else:
        words = text.split()
        base, extra = divmod(len(words), n)
        chunks = []
        pos = 0
        for i in range(n):
            size = base + (1 if i < extra else 0)
            chunks.append(" ".join(words[pos:pos + size]).strip())
            pos += size
    return [
        {"speaker": _text(turn.get("speaker")).lower(), "text": chunk}
        for turn, chunk in zip(turns, chunks) if chunk
    ]


def _single_pass_reels_and_tts(router, editorial_state, fact_state, video_state, notes, sure_saniye, ton, legacy_voice, log, baslangic_talimati=""):
    """Exactly one Reels text call + one TTS call in the normal path."""
    reels_state, model_reels = _original_reels_creative(
        router, editorial_state, fact_state, video_state, notes, sure_saniye,
        ton, log, KELIME_HIZI_ORANI, ek_talimat=baslangic_talimati or ""
    )
    reels_state = _pipeline._object_state_or_empty(reels_state)
    duo_plan = _pipeline._duo_plan_hazirla(reels_state, sure_saniye, ton, notes=notes)
    mode = str(duo_plan.get("mode") or duo_plan.get("uygunluk") or reels_state.get("anlatim_modu") or "DUO").upper()
    conversation_map = duo_plan.get("conversation_map") or []

    if mode in {"SOLO_FEMALE", "SOLO_MALE"}:
        speaker = "female" if mode == "SOLO_FEMALE" else "male"
        segments = [{"speaker": speaker, "text": reels_state.get("seslendirme_metni", "")}]
        duo_script = {
            "contract": {"mode": mode},
            "segments": segments,
            "model": "local-from-reels",
            "status": "ready" if segments else "fallback",
        }
    else:
        # Gerçek LLM tabanlı Duo diyalog script üretimini çağırıyoruz!
        log("🗣️ DUO modunda gerçek LLM tabanlı diyalog senaryosu üretiliyor...")
        duo_script = _pipeline._duo_script_calistir(
            router, duo_plan, editorial_state, fact_state, video_state, log,
            regeneration_instruction=""
        )
        segments = duo_script.get("segments") or []
        
        # Eğer diyalog üretimi başarısız olmuşsa, kural tabanlı eski bölme mekanizmasını
        # yedek (fallback) olarak devreye alıp sistemi çökmeden kurtarıyoruz!
        if not segments:
            log("⚠️ LLM tabanlı diyalog üretilemedi, kural tabanlı split_for_speakers fallback olarak devreye alınıyor...")
            segments = _split_for_speakers(reels_state.get("seslendirme_metni", ""), conversation_map)
            duo_script = {
                "contract": {"mode": mode},
                "segments": segments,
                "model": "local-fallback-split",
                "status": "ready" if segments else "fallback",
            }

    ses_path = gecici_ses_yolu()
    if mode == "DUO" and segments:
        ok, info = _pipeline._run_timed(
            log, "DUO multi-speaker TTS + WAV hazırlama",
            lambda: duo_ses_uret(router, segments, ses_path, log, hiz_carpani=SES_HIZ_CARPANI),
        )
        mod = "DUO"
    elif mode in {"SOLO_FEMALE", "SOLO_MALE"} and segments:
        solo_voice = _pipeline._mod_icin_legacy_ses(mode, legacy_voice)
        ok, info = _pipeline._run_timed(
            log, f"{mode} tek ses TTS + WAV hazırlama",
            lambda: router.ses_uret(segments[0]["text"], solo_voice, ses_path, log, hiz_carpani=SES_HIZ_CARPANI),
        )
        mod = mode
    else:
        log("❌ Konuşma segmentleri üretilemedi; TTS güvenli biçimde durduruldu.")
        ok, info, mod = False, None, mode

    if not ok or not os.path.exists(ses_path):
        temp_dosya_temizle(ses_path)
        return reels_state, model_reels, duo_plan, duo_script, False, None, mod, ""

    compatible, duration, ratio = _pipeline._ses_sure_uyumlu_mu(ses_path, sure_saniye)
    log(f"🎚️ TTS gerçek süre kontrolü: video {sure_saniye:.2f}s → ses {duration:.2f}s | oran {ratio:.2f}x")
    if not compatible:
        log("⚠️ TTS/video oranı ideal aralığın dışında; ikinci TTS üretimi yapılmayacak, mevcut ses FFmpeg senkronunda kullanılacak.")
    return reels_state, model_reels, duo_plan, duo_script, True, info, mod, ses_path


_pipeline._caption_calistir = _caption_guard
_pipeline._threads_calistir = _threads_guard
_pipeline._reels_ve_ses_uyumlu_uret = _single_pass_reels_and_tts
