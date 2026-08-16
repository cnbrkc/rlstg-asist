"""Telegram pipeline runtime guards.

The core pipeline stays untouched. This wrapper only enforces that social
outputs are present before Telegram delivery. Empty structured responses get
one controlled retry; if the model still returns an empty object, a deterministic
fact-locked fallback is built locally so the run never silently ships without
caption/Threads text.
"""

import pipeline as _pipeline

SOCIAL_REGEN_MAX = 1

_original_caption = _pipeline._caption_calistir
_original_threads = _pipeline._threads_calistir


def _text(value):
    return str(value or "").strip()


def _first_fact(fact_state):
    facts = (fact_state or {}).get("facts") if isinstance(fact_state, dict) else []
    if isinstance(facts, list):
        for item in facts:
            if isinstance(item, dict) and _text(item.get("fact")):
                status = _text(item.get("status")).upper()
                if status in {"OBSERVED", "VERIFIED"}:
                    return _text(item.get("fact"))
    return ""


def _model_identity(video_state):
    if not isinstance(video_state, dict):
        return ""
    ident = video_state.get("video_identity") or {}
    if not isinstance(ident, dict):
        return ""
    brand = _text(ident.get("brand"))
    model = _text(ident.get("exact_model"))
    if model and model.upper() != "UNKNOWN":
        return model
    if brand and brand.upper() != "UNKNOWN":
        return brand
    return ""


def _caption_fallback(reels_state, fact_state, editorial_state, video_state):
    """Small, fact-locked emergency caption; used only after model failure."""
    identity = _model_identity(video_state) or "bu araç"
    editorial = editorial_state if isinstance(editorial_state, dict) else {}
    core = _text(editorial.get("core_story"))
    why = _text(editorial.get("why_it_matters"))
    fact = _first_fact(fact_state)
    parts = [
        f"{identity}: videonun ötesinde asıl merak edilen taraf biraz da burada başlıyor.",
        core or fact or "Videoda öne çıkan detayları Fact Lock sınırları içinde takip ediyoruz.",
        why or fact,
        "Rakamlar ve görünen detaylar bir yana, otomobilde asıl mesele bunların gerçek kullanımda ne ifade ettiği.",
        "Siz olsanız bu noktada hangi detaya daha çok önem verirdiniz?",
    ]
    text = "\n\n".join(p for p in parts if p)
    return text[:900].rstrip(), ["otoxtra", "otomobil", "araba", "otomobilhaber", "arabasever"]


def _threads_fallback(fact_state, editorial_state, video_state):
    """Small, fact-locked emergency Threads text; no hashtag/question requirement."""
    identity = _model_identity(video_state) or "Bu araç"
    editorial = editorial_state if isinstance(editorial_state, dict) else {}
    discussion = _text(editorial.get("discussion_territory"))
    core = _text(editorial.get("core_story"))
    fact = _first_fact(fact_state)
    text = discussion or core or fact
    if not text:
        text = "Bu içerikte asıl mesele, videoda görünen detayın gerçek kullanımda ne ifade ettiği."
    return f"{identity} tarafında bence tartışma tam burada başlıyor: {text}"[:500].rstrip()


def _caption_guard(router, reels_state, fact_state, editorial_state, video_state, log):
    for attempt in range(SOCIAL_REGEN_MAX + 1):
        try:
            state, model = _original_caption(
                router, reels_state, fact_state, editorial_state, video_state, log
            )
        except Exception as exc:
            log(f"⚠️ Caption üretimi hata verdi: {str(exc)[:160]}")
            state, model = {"reels_aciklamasi": "", "reels_hashtagleri": []}, "hata"
        description = _text((state or {}).get("reels_aciklamasi"))
        hashtags = (state or {}).get("reels_hashtagleri") or []
        if description and hashtags:
            return state, model
        if attempt < SOCIAL_REGEN_MAX:
            log(f"⚠️ Caption eksik döndü; tek kontrollü yeniden üretim ({attempt + 1}/{SOCIAL_REGEN_MAX}).")

    description, hashtags = _caption_fallback(reels_state, fact_state, editorial_state, video_state)
    log("⚠️ Caption modeli boş kaldı; Fact Lock tabanlı yerel güvenli caption fallback kullanıldı.")
    return {"reels_aciklamasi": description, "reels_hashtagleri": hashtags}, "local-fallback"


def _threads_guard(router, video_state, fact_state, editorial_state, log):
    for attempt in range(SOCIAL_REGEN_MAX + 1):
        try:
            state, model = _original_threads(
                router, video_state, fact_state, editorial_state, log
            )
        except Exception as exc:
            log(f"⚠️ Threads üretimi hata verdi: {str(exc)[:160]}")
            state, model = {"threads_aciklamasi": ""}, "hata"
        text = _text((state or {}).get("threads_aciklamasi"))
        if text:
            return state, model
        if attempt < SOCIAL_REGEN_MAX:
            log(f"⚠️ Threads boş döndü; tek kontrollü yeniden üretim ({attempt + 1}/{SOCIAL_REGEN_MAX}).")

    text = _threads_fallback(fact_state, editorial_state, video_state)
    log("⚠️ Threads modeli boş kaldı; Fact Lock tabanlı yerel güvenli Threads fallback kullanıldı.")
    return {"threads_aciklamasi": text}, "local-fallback"


_pipeline._caption_calistir = _caption_guard
_pipeline._threads_calistir = _threads_guard

import telegram_pipeline_worker as _worker  # noqa: E402

_worker.main()
