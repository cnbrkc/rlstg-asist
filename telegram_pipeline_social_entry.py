"""Single Telegram pipeline entrypoint.

Social delivery is intentionally owned by telegram_pipeline_worker.  Keep this
launcher thin, but add one final defense before the production guard is loaded:
filesystem/TTS artifacts must never survive into social-copy generation.
"""

import re
import pipeline as _pipeline


def _text(value):
    return str(value or "").strip()


def _artifact(value):
    text = _text(value).lower()
    return (
        not text
        or text.startswith(("/tmp/", "/home/runner/", "data/", "./data/", "../"))
        or bool(re.search(r"\.(wav|mp3|m4a|aac|mp4|mov|webm)(?:\b|$)", text))
        or "\\tmp\\" in text
        or "\\home\\runner\\" in text
    )


_original_caption = _pipeline._caption_calistir


def _hard_caption_guard(router, reels_state, fact_state, editorial_state, video_state, log):
    state, model = _original_caption(router, reels_state, fact_state, editorial_state, video_state, log)
    state = state if isinstance(state, dict) else {}
    description = _text(state.get("reels_aciklamasi"))
    hashtags = state.get("reels_hashtagleri") or []
    if not _artifact(description) and hashtags:
        return state, model

    identity = "bu araç"
    if isinstance(video_state, dict):
        ident = video_state.get("video_identity") or {}
        if isinstance(ident, dict):
            identity = _text(ident.get("exact_model") or ident.get("brand")) or identity
    fallback = (
        f"{identity}: videonun ötesinde asıl merak edilen taraf burada başlıyor.\n\n"
        "Videodaki detayları Fact Lock sınırları içinde değerlendiriyoruz.\n\n"
        "Rakamlar kadar gerçek kullanımın ne söylediği de önemli."
    )
    log("⚠️ Final social guard: geçersiz/runtime Caption engellendi; güvenli Caption fallback kullanıldı.")
    return {"reels_aciklamasi": fallback, "reels_hashtagleri": ["otoxtra", "otomobil", "araba", "otomobilhaber", "arabasever"]}, "hard-local-fallback"


_pipeline._caption_calistir = _hard_caption_guard

# The production guard wraps this hardened caption function and then starts the worker.
import telegram_pipeline_guard  # noqa: F401,E402