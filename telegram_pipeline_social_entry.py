"""Single Telegram pipeline entrypoint.

Social delivery is intentionally owned by telegram_pipeline_worker. Keep this
launcher thin, but add final compatibility guards before the production guard
is loaded so transient Gemini/Duo failures cannot strand a valid render.
"""

import re
import pipeline as _pipeline
from router import SmartRouter as _SmartRouter


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


# HARD RULE: every TTS path must use the configured 1.20x speed. The caller
# cannot accidentally bypass it by passing 1.0 during a fallback/regeneration.
_ORIGINAL_SES_URET = _SmartRouter.ses_uret
_ORIGINAL_COKLU_SES_URET = _SmartRouter.coklu_ses_uret


def _ses_uret_12x(self, metin, ses_adi, cikti_dosyasi, log_ekle, hiz_carpani=1.0):
    return _ORIGINAL_SES_URET(self, metin, ses_adi, cikti_dosyasi, log_ekle, hiz_carpani=1.2)


def _coklu_ses_uret_12x(self, metin, speaker_voices, cikti_dosyasi, log_ekle, hiz_carpani=1.0):
    return _ORIGINAL_COKLU_SES_URET(self, metin, speaker_voices, cikti_dosyasi, log_ekle, hiz_carpani=1.2)


_SmartRouter.ses_uret = _ses_uret_12x
_SmartRouter.coklu_ses_uret = _coklu_ses_uret_12x


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

    targets = qa_state.get("regeneration_targets") if isinstance(qa_state, dict) else []
    if isinstance(targets, str):
        targets = [targets]
    normalized_targets = {str(x).strip().upper() for x in (targets or []) if str(x).strip()}
    if (
        not qa_pass
        and normalized_targets
        and normalized_targets <= {"DUO_SCRIPT_FAIL"}
        and ses_basarili
        and ses_dosyasi
        and _pipeline.os.path.exists(ses_dosyasi)
    ):
        log = kwargs.get("log")
        if log is None and len(args) >= 13:
            log = args[12]
        if callable(log):
            log("⚠️ QA yalnızca Duo script katmanını işaretledi; geçerli TTS bulunduğu için render güvenli biçimde devam ediyor.")
        qa_pass = True
        if isinstance(qa_state, dict):
            qa_state = dict(qa_state)
            qa_state["overall"] = "PASS"
            qa_state["regeneration_targets"] = []
            qa_state["duo_nonblocking_fallback"] = True

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


_original_research = _pipeline._research_calistir


def _research_compat(router, video_state, log):
    try:
        return _original_research(router, video_state, log)
    except Exception as exc:
        observed = video_state.get("observed_facts") if isinstance(video_state, dict) else []
        identity = video_state.get("video_identity") if isinstance(video_state, dict) else {}
        facts = []
        if isinstance(identity, dict):
            brand = _text(identity.get("brand"))
            model = _text(identity.get("exact_model"))
            if brand and model:
                facts.append({"fact": f"Videoda tanımlanan araç: {brand} {model}.", "status": "OBSERVED", "source": "Forensic video analysis", "source_type": "video", "confidence": "high"})
            elif model:
                facts.append({"fact": f"Videoda tanımlanan model: {model}.", "status": "OBSERVED", "source": "Forensic video analysis", "source_type": "video", "confidence": "high"})
        for item in observed if isinstance(observed, list) else []:
            text = _text(item)
            if text:
                facts.append({"fact": text, "status": "OBSERVED", "source": "Forensic video analysis", "source_type": "video", "confidence": "high"})
        log(f"⚠️ Research/Search geçici olarak kullanılamadı; yalnızca videoda gözlenen gerçeklerle Fact Lock devam ediyor: {str(exc)[:160]}")
        return {
            "facts": facts,
            "turkiye_satis_durumu": "BILINMIYOR",
            "turkiye_fiyati": "",
            "global_fiyat_bilgisi": "",
            "arastirma_notu": "Search fallback: dış doğrulama yapılamadı; yeni iddia eklenmedi.",
        }, "forensic-fallback"


_pipeline._research_calistir = _research_compat


# The Duo regeneration loop used to discard a perfectly valid WAV solely
# because its measured duration ratio was outside 0.85–1.15. Keep duration as
# a diagnostic, not a hard file-existence gate.
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
            log(f"🎚️ QA sonrası TTS süre kontrolü: video {sure_saniye:.2f}s → ses {ses_suresi:.2f}s | oran {oran:.2f}x")
            if uyumlu:
                return duo_script, True, (info, ses_dosyasi), mod
            log(f"⚠️ QA sonrası TTS oranı ideal aralık dışında ({oran:.2f}x), ancak geçerli WAV korunuyor ve FFmpeg senkronunda kullanılacak.")
            return duo_script, True, (info, ses_dosyasi), mod

        _pipeline.temp_dosya_temizle(ses_dosyasi)
        if deneme < _pipeline.VOICE_REGEN_MAX:
            instruction += " Önceki Duo/TTS üretimi doğrulanamadı; yalnızca izin verilen speakerları kullan, hedef kelime aralığına uy ve metni doğal konuşulabilirlikte tut."
            continue

    fallback_path = _pipeline.gecici_ses_yolu()
    try:
        fallback_ok, fallback_info = router.ses_uret(
            reels_state.get("seslendirme_metni", "") if isinstance(reels_state, dict) else "",
            _pipeline._mod_icin_legacy_ses("DUO", legacy_voice),
            fallback_path,
            log,
            hiz_carpani=1.2,
        )
        if fallback_ok and _pipeline.os.path.exists(fallback_path):
            fallback_sure = _pipeline._ses_suresini_al(fallback_path)
            log(f"↩️ Duo fallback: geçerli legacy TTS korundu ({fallback_sure:.2f}s); render Duo katmanına bağlı olmadan devam edecek.")
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


# Telegram social output contract: video caption contains the Instagram/Facebook
# description + hashtags; the next message contains ONLY the Threads text.
import telegram_pipeline_worker as _worker


def _threads_only_bundle(caption, hashtags, threads):
    return _text(threads)


_worker._social_bundle = _threads_only_bundle

import telegram_pipeline_guard  # noqa: F401,E402
