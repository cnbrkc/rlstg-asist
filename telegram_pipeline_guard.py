"""Telegram pipeline runtime guards.

Keeps the existing pipeline intact while enforcing a hard invariant:
caption and Threads outputs must be non-empty before a successful run can
reach Telegram delivery. Empty structured-output responses are retried with
the exact same prompts/context; Fact Lock, Threads prompt and TTS/FFmpeg are
not changed.
"""

import pipeline as _pipeline

SOCIAL_REGEN_MAX = 2

_original_caption = _pipeline._caption_calistir
_original_threads = _pipeline._threads_calistir


def _caption_guard(router, reels_state, fact_state, editorial_state, video_state, log):
    last = ({"reels_aciklamasi": "", "reels_hashtagleri": []}, "hata")
    for attempt in range(SOCIAL_REGEN_MAX + 1):
        state, model = _original_caption(
            router, reels_state, fact_state, editorial_state, video_state, log
        )
        last = (state, model)
        description = str((state or {}).get("reels_aciklamasi", "") or "").strip()
        hashtags = (state or {}).get("reels_hashtagleri") or []
        if description:
            if not hashtags:
                log("⚠️ Caption üretildi ancak hashtag listesi boş; aynı caption katmanı kontrollü olarak yeniden deneniyor.")
                continue
            return state, model
        if attempt < SOCIAL_REGEN_MAX:
            log(f"⚠️ Caption boş döndü; caption üretimi yeniden deneniyor ({attempt + 1}/{SOCIAL_REGEN_MAX}).")
    raise RuntimeError("Caption üretimi boş kaldı; eksik sosyal çıktı ile pipeline tamamlanmayacak.")


def _threads_guard(router, video_state, fact_state, editorial_state, log):
    last = ({"threads_aciklamasi": ""}, "hata")
    for attempt in range(SOCIAL_REGEN_MAX + 1):
        state, model = _original_threads(
            router, video_state, fact_state, editorial_state, log
        )
        last = (state, model)
        text = str((state or {}).get("threads_aciklamasi", "") or "").strip()
        if text:
            return state, model
        if attempt < SOCIAL_REGEN_MAX:
            log(f"⚠️ Threads boş döndü; Threads üretimi yeniden deneniyor ({attempt + 1}/{SOCIAL_REGEN_MAX}).")
    raise RuntimeError("Threads üretimi boş kaldı; eksik sosyal çıktı ile pipeline tamamlanmayacak.")


_pipeline._caption_calistir = _caption_guard
_pipeline._threads_calistir = _threads_guard

import telegram_pipeline_worker as _worker  # noqa: E402

_worker.main()
