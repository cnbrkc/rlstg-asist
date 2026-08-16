"""Telegram pipeline runtime guards.

The guard keeps the production pipeline feature-complete while removing the
expensive pre-TTS Duo-script LLM round-trip. Reels Creative already returns
both the voiceover text and the conversation map, so the speaker script is
constructed locally and sent to Gemini multi-speaker TTS exactly once.
"""

import re
import os
import pipeline as _pipeline
from media import gecici_ses_yolu, temp_dosya_temizle, _ses_suresini_al
from config import SES_HIZ_CARPANI, KELIME_HIZI_ORANI
from duo_audio import duo_ses_uret

SOCIAL_REGEN_MAX = 1

_original_caption = _pipeline._caption_calistir
_original_threads = _pipeline._threads_calistir
_original_reels_creative = _pipeline._reels_creative_calistir


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
    return "\n\n".join(p for p in parts if p)[:900].rstrip(), ["otoxtra", "otomobil", "araba", "otomobilhaber", "arabasever"]


def _threads_fallback(fact_state, editorial_state, video_state):
    identity = _model_identity(video_state) or "Bu araç"
    editorial = editorial_state if isinstance(editorial_state, dict) else {}
    discussion = _text(editorial.get("discussion_territory"))
    core = _text(editorial.get("core_story"))
    fact = _first_fact(fact_state)
    text = discussion or core or fact or "Bu içerikte asıl mesele, videoda görünen detayın gerçek kullanımda ne ifade ettiği."
    return f"{identity} tarafında bence tartışma tam burada başlıyor: {text}"[:500].rstrip()


def _caption_guard(router, reels_state, fact_state, editorial_state, video_state, log):
    for attempt in range(SOCIAL_REGEN_MAX + 1):
        try:
            state, model = _original_caption(router, reels_state, fact_state, editorial_state, video_state, log)
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
            state, model = _original_threads(router, video_state, fact_state, editorial_state, log)
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


def _split_for_speakers(text, conversation_map):
    """Split one already-approved voiceover text into the planned speaker turns.

    No new wording is generated here: every token comes from the single Reels
    Creative voiceover text. This removes the second LLM text-generation pass.
    """
    text = _text(text)
    turns = [x for x in (conversation_map or []) if isinstance(x, dict) and _text(x.get("speaker"))]
    if not text or not turns:
        return []
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    if len(sentences) < len(turns):
        words = text.split()
        n = len(turns)
        base, extra = divmod(len(words), n)
        chunks = []
        pos = 0
        for i in range(n):
            size = base + (1 if i < extra else 0)
            chunks.append(" ".join(words[pos:pos + size]).strip())
            pos += size
    else:
        n = len(turns)
        base, extra = divmod(len(sentences), n)
        chunks = []
        pos = 0
        for i in range(n):
            size = base + (1 if i < extra else 0)
            chunks.append(" ".join(sentences[pos:pos + size]).strip())
            pos += size
    result = []
    for turn, chunk in zip(turns, chunks):
        if chunk:
            result.append({"speaker": _text(turn.get("speaker")).lower(), "text": chunk})
    return result


def _single_pass_reels_and_tts(router, editorial_state, fact_state, video_state, notes, sure_saniye, ton, legacy_voice, log, baslangic_talimati=""):
    """One Reels text generation + one multi-speaker TTS request.

    Duration is validated after TTS, but no automatic second Reels/TTS generation
    is performed here. FFmpeg's existing speed-sync path remains responsible for
    small duration differences, preserving the established render behavior.
    """
    reels_state, model_reels = _original_reels_creative(
        router, editorial_state, fact_state, video_state, notes, sure_saniye,
        ton, log, KELIME_HIZI_ORANI, ek_talimat=baslangic_talimati or ""
    )
    reels_state = _pipeline._object_state_or_empty(reels_state)
    duo_plan = _pipeline._duo_plan_hazirla(reels_state, sure_saniye, ton)
    mode = str(duo_plan.get("mode") or duo_plan.get("uygunluk") or reels_state.get("anlatim_modu") or "DUO").upper()
    conversation_map = duo_plan.get("conversation_map") or []

    # For solo modes, preserve the existing single-voice path. For DUO, the
    # approved Reels text is split locally according to the model's own map.
    if mode in {"SOLO_FEMALE", "SOLO_MALE"}:
        speaker = "female" if mode == "SOLO_FEMALE" else "male"
        duo_script = {"contract": {"mode": mode}, "segments": [{"speaker": speaker, "text": reels_state.get("seslendirme_metni", "")}], "model": "local-from-reels", "status": "ready"}
    else:
        segments = _split_for_speakers(reels_state.get("seslendirme_metni", ""), conversation_map)
        duo_script = {"contract": {"mode": "DUO"}, "segments": segments, "model": "local-from-reels", "status": "ready" if segments else "fallback"}

    if not duo_script.get("segments"):
        log("⚠️ Conversation map boş; mevcut legacy tek sesli TTS yolu kullanılıyor.")
        ses_path = gecici_ses_yolu()
        ok, info = router.ses_uret(reels_state.get("seslendirme_metni", ""), legacy_voice, ses_path, log, hiz_carpani=SES_HIZ_CARPANI)
        mod = "LEGACY"
    else:
        ses_path = gecici_ses_yolu()
        ok, info, _ = duo_ses_uret(router, duo_script["segments"], ses_path, log, hiz_carpani=SES_HIZ_CARPANI)
        mod = mode

    if not ok or not os.path.exists(ses_path):
        temp_dosya_temizle(ses_path)
        return reels_state, model_reels, duo_plan, duo_script, False, None, mod, ""

    compatible, duration, ratio = _pipeline._ses_sure_uyumlu_mu(ses_path, sure_saniye)
    log(f"🎚️ TTS gerçek süre kontrolü: video {sure_saniye:.2f}s → ses {duration:.2f}s | oran {ratio:.2f}x")
    # Keep the generated TTS even when the ratio is slightly outside the old
    # gate. The existing FFmpeg renderer already performs duration synchronization.
    if not compatible:
        log("⚠️ TTS/video oranı ideal aralığın dışında; ikinci TTS üretimi yapılmayacak, mevcut ses FFmpeg senkronunda kullanılacak.")
    return reels_state, model_reels, duo_plan, duo_script, True, info, mod, ses_path


_pipeline._caption_calistir = _caption_guard
_pipeline._threads_calistir = _threads_guard
_pipeline._reels_ve_ses_uyumlu_uret = _single_pass_reels_and_tts

import telegram_pipeline_worker as _worker  # noqa: E402

_worker.main()
