"""Single Telegram pipeline entrypoint.

Social delivery is intentionally owned by telegram_pipeline_worker. Keep this
launcher thin, but add final compatibility guards before the production guard
is loaded so transient Gemini/Duo failures cannot strand a valid render.
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


# pipeline_calistir() expects a normalized tuple; keep this boundary guard in
# place because the worker imports this launcher before starting production.
_original_qa_regeneration_loop = _pipeline._qa_regeneration_loop


def _qa_regeneration_loop_compat(*args, **kwargs):
    (
        reels_state,
        caption_state,
        threads_state,
        duo_plan,
        duo_script,
        ses_basarili,
        kullanilan_ses_modeli,
        ses_modu,
        ses_dosyasi,
        qa_state,
        qa_rounds,
        model_reels,
        model_caption,
        model_threads,
        qa_pass,
    ) = _original_qa_regeneration_loop(*args, **kwargs)

    return (
        reels_state,
        model_reels,
        duo_plan,
        duo_script,
        ses_basarili,
        kullanilan_ses_modeli,
        ses_modu,
        ses_dosyasi,
        caption_state,
        threads_state,
        qa_state,
        qa_rounds,
        model_caption,
        model_threads,
        qa_pass,
    )


_pipeline._qa_regeneration_loop = _qa_regeneration_loop_compat


# The Duo regeneration loop used to discard a perfectly valid WAV solely
# because its measured duration ratio was outside 0.85–1.15. That made the
# final renderer fail even though FFmpeg can safely synchronize the audio.
# Keep duration as a diagnostic, not a hard file-existence gate.
def _duo_ve_ses_yenile_compat(router, reels_state, duo_plan, editorial_state, fact_state, video_state, sure_saniye, legacy_voice, log, regen_instruction):
    instruction = regen_instruction or (
        "Duo script QA tarafından başarısız bulundu. Aynı Fact Lock ve seçili içerik tonunu koruyarak "
        "daha kısa, doğal ve konuşulabilir bir script üret."
    )
    last_duo = {"status": "fallback", "contract": duo_plan or {}, "segments": []}
    last_mod = "LEGACY"

    for deneme in range(_pipeline.VOICE_REGEN_MAX + 1):
        duo_script = _pipeline._duo_script_calistir(
            router, duo_plan, editorial_state, fact_state, video_state, log,
            regeneration_instruction=instruction,
        )
        last_duo = duo_script

        ses_dosyasi = _pipeline.gecici_ses_yolu()
        ok, info, mod = _pipeline._duo_ses_veya_legacy_uret(
            router,
            duo_script,
            reels_state.get("seslendirme_metni", "") if isinstance(reels_state, dict) else "",
            legacy_voice,
            log,
            ses_dosyasi,
        )
        last_mod = mod

        if ok and _pipeline.os.path.exists(ses_dosyasi):
            uyumlu, ses_suresi, oran = _pipeline._ses_sure_uyumlu_mu(ses_dosyasi, sure_saniye)
            log(
                f"🎚️ QA sonrası TTS süre kontrolü: video {sure_saniye:.2f}s → "
                f"ses {ses_suresi:.2f}s | oran {oran:.2f}x"
            )
            if uyumlu:
                return duo_script, True, (info, ses_dosyasi), mod

            # Valid WAV exists. Do NOT delete it just because timing is outside
            # the preferred range; FFmpeg synchronization handles this safely.
            log(
                f"⚠️ QA sonrası TTS oranı ideal aralık dışında ({oran:.2f}x), "
                "ancak geçerli WAV korunuyor ve FFmpeg senkronunda kullanılacak."
            )
            return duo_script, True, (info, ses_dosyasi), mod

        _pipeline.temp_dosya_temizle(ses_dosyasi)
        if deneme < _pipeline.VOICE_REGEN_MAX:
            instruction += (
                " Önceki Duo/TTS üretimi doğrulanamadı; yalnızca izin verilen speakerları kullan, "
                "hedef kelime aralığına uy ve metni doğal konuşulabilirlikte tut."
            )
            continue

    # Absolute last-resort production path: a valid legacy TTS is preferable to
    # failing the entire video because the optional Duo layer could not validate.
    fallback_path = _pipeline.gecici_ses_yolu()
    try:
        fallback_ok, fallback_info = router.ses_uret(
            reels_state.get("seslendirme_metni", "") if isinstance(reels_state, dict) else "",
            _pipeline._mod_icin_legacy_ses("DUO", legacy_voice),
            fallback_path,
            log,
            hiz_carpani=_pipeline.SES_HIZ_CARPANI,
        )
        if fallback_ok and _pipeline.os.path.exists(fallback_path):
            fallback_sure = _pipeline._ses_suresini_al(fallback_path)
            log(
                f"↩️ Duo fallback: geçerli legacy TTS korundu ({fallback_sure:.2f}s); "
                "render Duo katmanına bağlı olmadan devam edecek."
            )
            return last_duo, True, (fallback_info, fallback_path), "LEGACY_DUO"
    except Exception as exc:
        log(f"⚠️ Legacy Duo fallback başarısız: {str(exc)[:180]}")
    _pipeline.temp_dosya_temizle(fallback_path)
    return last_duo, False, None, last_mod


_pipeline._duo_ve_ses_yenile = _duo_ve_ses_yenile_compat


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

import telegram_pipeline_guard  # noqa: F401,E402
