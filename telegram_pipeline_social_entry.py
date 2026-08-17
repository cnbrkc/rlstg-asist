"""Single Telegram pipeline entrypoint.

Social delivery is intentionally owned by telegram_pipeline_worker. Keep this
launcher thin, but add final compatibility guards before the production guard
is loaded so transient Gemini/Duo failures cannot strand a valid render.
"""

import re
import pipeline as _pipeline
from router import SmartRouter as _SmartRouter
import media as _media


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
_ORIGINAL_TTS_PROMPT = _SmartRouter._tts_performans_promptu_olustur


def _tts_performans_promptu_human(self, metin, ses_adi):
    """Gemini TTS director prompt based on Google's current TTS guidance."""
    return (
        "SYNTHESIZE SPEECH ONLY. Do not read these instructions aloud. "
        "The text under TRANSCRIPT is the only spoken content.\n\n"
        "# AUDIO PROFILE\n"
        "You are a Turkish female automotive presenter using the Autonoe voice. "
        "Autonoe is bright, but the performance must feel like a real person talking "
        "to another car enthusiast, not a commercial, news bulletin, audiobook or "
        "robotic TTS demo.\n\n"
        "# SCENE\n"
        "A casual otoXtra automotive conversation recorded close to the listener. "
        "You are genuinely reacting to the car and telling the listener what matters. "
        "You can sound curious, impressed, skeptical, amused or serious when the words "
        "justify it. Keep the emotion controlled and believable.\n\n"
        "# DIRECTOR'S NOTES\n"
        "Style: warm, natural, conversational, confident and lively Turkish automotive "
        "presenter. Use a subtle vocal smile where appropriate. Never sound synthetic, "
        "flat, overly polished, theatrical or like an announcement.\n"
        "Dynamics: vary emphasis naturally. Important facts should sound intentional; "
        "surprising details can carry a brief lift in energy; conclusions can settle "
        "naturally. Do not keep one fixed pitch or energy level throughout.\n"
        "Pacing: conversational short-form pace, but not rushed. Let punctuation and "
        "ellipses create micro-pauses. Use a brief pause at meaningful transitions. "
        "Do not remove every pause and do not create long dead air.\n"
        "Breathing: natural micro-breaths between thought groups. Do not breathe after "
        "every sentence and do not force audible breathing.\n"
        "Pronunciation: Turkish should sound fluent and effortless. Preserve names, "
        "model names, numbers and technical terms exactly as written.\n"
        "Audio tags: the transcript may contain a small number of official English "
        "audio tags such as [curious], [excitedly], [serious], [sighs], [laughs] or "
        "[short pause]. Treat them as performance controls, not spoken words. Use them "
        "only where they fit the meaning. Never invent extra words or commentary.\n\n"
        "# PERFORMANCE RULES\n"
        "Keep the exact spoken words from the transcript. Do not paraphrase, omit or "
        "add content. Preserve line order. Make the delivery feel acted, not read. "
        "Use restrained emotional variation instead of exaggerated acting.\n\n"
        "# TRANSCRIPT\n"
        f"{metin}"
    )


_SmartRouter._tts_performans_promptu_olustur = _tts_performans_promptu_human


def _ses_uret_12x_autonoe(self, metin, ses_adi, cikti_dosyasi, log_ekle, hiz_carpani=1.0):
    log_ekle("🎙️ TTS modu: SOLO_FEMALE → Autonoe | zorunlu hız 1.20x")
    return _ORIGINAL_SES_URET(self, metin, "Autonoe", cikti_dosyasi, log_ekle, hiz_carpani=1.2)


def _coklu_ses_uret_12x(self, metin, speaker_voices, cikti_dosyasi, log_ekle, hiz_carpani=1.0):
    # Kept only as a compatibility guard for callers outside the main pipeline.
    return _ORIGINAL_COKLU_SES_URET(self, metin, speaker_voices, cikti_dosyasi, log_ekle, hiz_carpani=1.2)


_SmartRouter.ses_uret = _ses_uret_12x_autonoe
_SmartRouter.coklu_ses_uret = _coklu_ses_uret_12x


# The current Reels prompt originally prohibited audio tags. Gemini's official
# TTS guidance explicitly supports English audio tags for emotion, pacing and
# non-verbal delivery, so add a runtime director layer without rewriting the
# existing content/word-count/SEO prompt files.
_ORIGINAL_REELS_PROMPT = _pipeline.reels_creative_promptunu_olustur


def _reels_prompt_tts_director(sure_saniye, icerik_tonu, kelime_hizi_orani=None, ek_talimat=""):
    base = _ORIGINAL_REELS_PROMPT(sure_saniye, icerik_tonu, kelime_hizi_orani, ek_talimat=ek_talimat)
    ton = str(icerik_tonu or "dengeli").strip().lower()
    if ton == "eglence":
        mood = "oyuncu, meraklı ve canlı; ama influencer gibi bağırmadan"
    elif ton == "bilgi":
        mood = "kendinden emin, meraklı ve açıklayıcı; kritik bilgilerde kontrollü vurgu"
    elif ton == "teknik":
        mood = "net, kendinden emin ve teknik olarak odaklı; önemli sonuçlarda doğal vurgu"
    else:
        mood = "meraklı, sıcak, kendinden emin ve ölçülü canlı"
    return base + (
        "\n\n🚨 TTS PERFORMANS KİLİDİ — GEMINI NATIVE TTS UYUMLU\n"
        "Bu metin doğrudan Gemini TTS'ye gidecek. Yukarıdaki içerik tonu, bilgi doğruluğu, "
        "kelime aralığı ve yaratıcı kurallar aynen korunur; aşağıdaki bölüm yalnızca NASIL "
        "söyleneceğini düzenler.\n"
        f"- Genel performans: {mood}.\n"
        "- Metni haber spikeri gibi değil, arabadan anlayan birinin arkadaşına anlattığı "
        "gibi yaz. Cümle uzunluklarını rahat nefes alınabilecek şekilde tut.\n"
        "- Duygu tek düze olmasın: hook'ta merak/enerji, güçlü bulguda kontrollü şaşkınlık, "
        "yorumda güven, kapanışta doğal kesinlik gibi küçük performans değişimleri kullan.\n"
        "- Gerçek bir durak gereken yerde üç nokta (…) veya doğal noktalama kullan; önemli "
        "geçişlerde en fazla birkaç kez [short pause] kullanılabilir. Her cümleye durak koyma.\n"
        "- Gemini TTS'nin resmi audio-tag yaklaşımını kullanabilirsin. Yalnızca anlamlı "
        "yerlerde ve seyrek biçimde İngilizce tag kullan: [curious], [excitedly], [serious], "
        "[amused], [sighs], [laughs], [short pause]. Tagleri konuşulan kelime gibi sayma.\n"
        "- 20 saniye civarı bir Reels için yaklaşık 2-4 performans tag'i yeterlidir; metni "
        "tag çöplüğüne çevirme. Uzun videolarda da ölçülü kal. [laughs]/[sighs] yalnızca "
        "metnin duygusu bunu gerçekten destekliyorsa kullan.\n"
        "- [vurgu] gibi bizim uydurduğumuz yönergeleri kullanma. Audio tagler İngilizce ve "
        "Gemini TTS'nin desteklediği biçimde olmalı.\n"
        "- Tagler ve noktalama kelime hedefini bozmasın; gerçek konuşma kelime sayısı hedef "
        "aralıkta kalmalı.\n"
    )


_pipeline.reels_creative_promptunu_olustur = _reels_prompt_tts_director


# Audio tags are control markup, not spoken words. Do not let them inflate the
# existing word-count contract that controls script duration.
_ORIGINAL_KELIME_SAYISI = _pipeline._kelime_sayisi


def _kelime_sayisi_tagsiz(metin):
    tagsiz = re.sub(r"\[[A-Za-z][A-Za-z ,'-]{0,80}\]", " ", str(metin or ""))
    return _ORIGINAL_KELIME_SAYISI(tagsiz)


_pipeline._kelime_sayisi = _kelime_sayisi_tagsiz


# Production choice: otoXtra Telegram currently uses one female voice only.
# Keep the existing Duo data structures for compatibility/QA, but force the
# actual plan and audio renderer to SOLO_FEMALE + Autonoe. No second voice is
# synthesized in the production path.
_ORIGINAL_DUO_PLAN_HAZIRLA = _pipeline._duo_plan_hazirla


def _solo_plan_hazirla(reels_state, sure_saniye, ton):
    plan = _ORIGINAL_DUO_PLAN_HAZIRLA(reels_state, sure_saniye, ton)
    plan = dict(plan or {})
    plan.update({
        "mode": "SOLO_FEMALE",
        "hook_speaker": "female",
        "ending_speaker": "female",
        "female_weight": 1.0,
        "male_weight": 0.0,
        "interaction_level": 0.0,
        "tension_level": 0.0,
        "content_tone": str(ton or "dengeli").strip().lower() or "dengeli",
        "rationale": "Telegram production contract: single Autonoe voice only.",
    })
    return plan


_pipeline._duo_plan_hazirla = _solo_plan_hazirla


def _single_voice_tts_uret(router, duo_script, legacy_text, legacy_voice, log, output_path):
    ok, info = router.ses_uret(
        legacy_text,
        "Autonoe",
        output_path,
        log,
        hiz_carpani=1.2,
    )
    return ok, info, "SOLO_FEMALE"


_pipeline._duo_ses_veya_legacy_uret = _single_voice_tts_uret


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
            log("⚠️ QA yalnızca legacy Duo katmanını işaretledi; production TTS tek ses Autonoe olduğu için geçerli TTS ile render devam ediyor.")
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
# a diagnostic, not a hard file-existence gate. Production audio is still single
# Autonoe because _duo_ses_veya_legacy_uret is patched above.
def _duo_ve_ses_yenile_compat(router, reels_state, duo_plan, editorial_state, fact_state, video_state, sure_saniye, legacy_voice, log, regen_instruction):
    instruction = regen_instruction or (
        "Solo Autonoe TTS script QA tarafından yeniden üretilecek. Aynı Fact Lock ve seçili içerik tonunu koruyarak "
        "daha kısa, doğal, duygulu ve konuşulabilir bir script üret."
    )
    last_duo = {"status": "fallback", "contract": dict(duo_plan or {}), "segments": []}
    last_mod = "SOLO_FEMALE"

    for deneme in range(_pipeline.VOICE_REGEN_MAX + 1):
        duo_script = _pipeline._duo_script_calistir(
            router, duo_plan, editorial_state, fact_state, video_state, log,
            regeneration_instruction=instruction,
        )
        last_duo = duo_script
        ses_dosyasi = _pipeline.gecici_ses_yolu()
        ok, info, mod = _single_voice_tts_uret(
            router,
            duo_script,
            reels_state.get("seslendirme_metni", "") if isinstance(reels_state, dict) else "",
            "Autonoe",
            log,
            ses_dosyasi,
        )
        last_mod = mod

        if ok and _pipeline.os.path.exists(ses_dosyasi):
            _, ses_suresi, oran = _pipeline._ses_sure_uyumlu_mu(ses_dosyasi, sure_saniye)
            log(f"🎚️ QA sonrası TTS süre kontrolü: video {sure_saniye:.2f}s → ses {ses_suresi:.2f}s | oran {oran:.2f}x")
            return duo_script, True, (info, ses_dosyasi), mod

        _pipeline.temp_dosya_temizle(ses_dosyasi)
        if deneme < _pipeline.VOICE_REGEN_MAX:
            instruction += " Önceki TTS üretimi doğrulanamadı; doğal konuşma ritmi, duygu ve hedef kelime aralığını koru."
            continue

    return last_duo, False, None, last_mod


_pipeline._duo_ve_ses_yenile = _duo_ve_ses_yenile_compat


# Restore the proven video/audio synchronization behavior from mainbackup.
# If TTS is shorter, speed the video up; if TTS is longer, slow the video down.
# Keep the same safe 0.5x–1.5x bounds and the existing quality/FPS pipeline.
def _video_ve_sesi_birlestir_sync(video_yolu, ses_yolu, cikti_yolu, log_ekle):
    if not ses_yolu or not _pipeline.os.path.exists(ses_yolu):
        return False
    input_bilgi = _media.medya_raporu(video_yolu, "INPUT", log_ekle)
    _media.medya_raporu(ses_yolu, "TTS 1.20x SONRASI", log_ekle)
    video_sure = _media.video_suresini_al(video_yolu)
    ses_sure = _media._ses_suresini_al(ses_yolu)
    video_filtresi = None

    if video_sure > 0 and ses_sure > 0:
        oran = video_sure / ses_sure
        if abs(oran - 1.0) >= 0.02:
            uygulanan_oran = max(_media.MIN_VIDEO_YAVASLATMA, min(_media.MAKS_VIDEO_HIZLANDIRMA, oran))
            if abs(uygulanan_oran - 1.0) >= 0.005:
                video_filtresi = f"setpts=PTS/{uygulanan_oran:.6f}"
                log_ekle(
                    f"🎚️ Ses/video süre uyumu: video {video_sure:.2f}s → ses {ses_sure:.2f}s | görüntü hızı {uygulanan_oran:.2f}x"
                )
            if abs(oran - uygulanan_oran) > 0.01:
                log_ekle(
                    f"⚠️ Süre farkı {oran:.2f}x sınırın dışında; güvenli {uygulanan_oran:.2f}x sınırı kullanıldı."
                )
        else:
            log_ekle(f"🎚️ Ses/video süre uyumu: fark küçük ({abs(video_sure-ses_sure):.2f}s), hız değişimi yapılmadı.")

    kalite_filtresi, _ = _media._kalite_filtresi_olustur(input_bilgi, log_ekle)
    if kalite_filtresi:
        video_filtresi = f"{video_filtresi},{kalite_filtresi}" if video_filtresi else kalite_filtresi

    output_fps = input_bilgi.get("fps") or 30.0
    komut = [_media.FFMPEG_BIN, "-y", "-i", video_yolu, "-i", ses_yolu]
    if video_filtresi:
        komut += ["-filter:v", video_filtresi]
    komut += [
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", _media.VIDEO_PRESET, "-crf", str(_media.VIDEO_CRF),
        "-pix_fmt", "yuv420p", "-r", f"{output_fps:.6f}",
        "-c:a", "aac", "-ar", str(_media.FINAL_AUDIO_SAMPLE_RATE), "-ac", str(_media.SES_KANAL),
        "-b:a", _media.FINAL_AUDIO_BITRATE, "-shortest", cikti_yolu,
    ]
    try:
        r = _media.subprocess.run(komut, capture_output=True, text=True, timeout=_media.FFMPEG_TIMEOUT)
        if r.returncode != 0:
            log_ekle(f"⚠️ Video render ffmpeg hatası: {(r.stderr or '')[-800:]}")
            return False
        _media.medya_raporu(cikti_yolu, "OUTPUT", log_ekle)
        return _pipeline.os.path.exists(cikti_yolu) and _pipeline.os.path.getsize(cikti_yolu) > 0
    except Exception as e:
        log_ekle(f"⚠️ Video render hatası: {e}")
        return False


_pipeline.video_ve_sesi_birlestir = _video_ve_sesi_birlestir_sync


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
