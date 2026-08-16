        if "❌" in text or "hata" in lower or "error" in lower:
            errors.append(text)
        if text.startswith("📐 ") or text.startswith("🎚️ "):
            warnings.append(text)

    def progress(n, total, msg):
        step_status[n - 1] = "🟢"
        current = PIPELINE_STEPS[n - 1] if 0 < n <= len(PIPELINE_STEPS) else str(msg)
        try:
            edit_message(loading_id, _loading_text(n - 1, f"Tamamlandı → {current}", len(warnings), len(errors)))
        except Exception as exc:
            print(f"Loading mesajı güncellenemedi: {exc}", flush=True)

    try:
        result = pipeline_calistir(router=router, video_bytes=raw, mime_type=mime, temp_input_video=str(path), video_analiz_notlari=video_note, metin_uretim_notlari=video_note, sure_saniye=duration, icerik_tonu=selected_tone, secilen_ses_ingilizce="Autonoe", log_ekle=log, ilerlemeyi_guncelle=progress)
    except Exception as exc:
        errors.append(str(exc))
        try:
            edit_message(loading_id, _final_report(step_status, warnings, errors, {"secilen_ses_ingilizce": "Autonoe"}, tone_key))
        except Exception:
            pass
        raise

    final = result.get("final_video")
    if not final or not Path(final).exists():
        errors.append("Pipeline tamamlandı ancak final video üretilemedi.")
        edit_message(loading_id, _final_report(step_status, warnings, errors, result, tone_key))
        raise RuntimeError("Pipeline tamamlandı ancak final video üretilemedi.")

    result["sync_note"] = next((x for x in warnings if "senkron" in x.lower() or "süre uyumu" in x.lower()), "Süre kontrolü yapıldı")
    caption = result.get("reels_aciklamasi") or ""
    hashtags = result.get("reels_hashtagleri") or []
    if not caption.strip():
        warnings.append("⚠️ Instagram/Facebook açıklaması boş üretildi.")
    if not hashtags:
        warnings.append("⚠️ Hashtag listesi boş üretildi.")
    video_caption, caption_truncated = _caption_with_hashtags(caption, hashtags)
    if caption_truncated:
        warnings.append(f"⚠️ Telegram video caption sınırı ({TELEGRAM_VIDEO_CAPTION_LIMIT} karakter): açıklama kısaltıldı; hashtagler korunarak sona alındı.")
    for i in range(len(PIPELINE_STEPS)):
        step_status.setdefault(i, "🟢")
    edit_message(loading_id, _final_report(step_status, warnings, errors, result, tone_key))
    send_video(final, video_caption)
    send_message(_format_title_options(result.get("kapak_basliklari") or []))
    threads = result.get("threads_aciklamasi") or ""
    send_message(threads if threads else "Açıklama üretilemedi.")
    Path("pipeline_result.json").write_text(json.dumps({"source": path.name, "final_video": Path(final).name, "content_tone": tone_key, "video_note": user_video_note, "seslendirme": result.get("seslendirme_metni", ""), "caption": caption, "caption_telegram": video_caption, "title_options": result.get("kapak_basliklari", []), "threads": threads, "qa": result.get("qa_result", {}), "warnings": warnings, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")


def process_text(text):
    text = (text or "").strip()
    initial = send_message(_loading_text(0, "Metin alındı, text-only pipeline başlatılıyor...", steps=TEXT_PIPELINE_STEPS))
    loading_id = initial["result"]["message_id"]
    router = SmartRouter()
    step_status = {}
    warnings = []
    errors = []
    tone_key = os.environ.get("CONTENT_TONE", "dengeli").strip().lower()
    tone_key = tone_key if tone_key in TON_MAP else "dengeli"
    selected_tone = TON_MAP[tone_key]

    def log(msg):
        text_msg = str(msg).strip()
        print(text_msg, flush=True)
        lower = text_msg.lower()
        if "⚠️" in text_msg or "uyarı" in lower or "warning" in lower:
            warnings.append(text_msg)
        if "❌" in text_msg or "hata" in lower or "error" in lower:
            errors.append(text_msg)

    def progress(n, total, msg):
        step_status[n - 1] = "🟢"
        current = TEXT_PIPELINE_STEPS[n - 1] if 0 < n <= len(TEXT_PIPELINE_STEPS) else str(msg)
        try:
            edit_message(loading_id, _loading_text(n - 1, f"Tamamlandı → {current}", len(warnings), len(errors), steps=TEXT_PIPELINE_STEPS))
        except Exception as exc:
            print(f"Loading mesajı güncellenemedi: {exc}", flush=True)

    try:
        result = metin_pipeline_calistir(router=router, metin=text, icerik_tonu=selected_tone, secilen_ses_ingilizce="Autonoe", log_ekle=log, ilerlemeyi_guncelle=progress)
    except Exception as exc:
        errors.append(str(exc))
        try:
            edit_message(loading_id, _final_text_report(step_status, warnings, errors, {"secilen_ses_ingilizce": "Autonoe", "qa_result": {}}, tone_key))
        except Exception:
            pass
        raise

    audio = result.get("ses_dosyasi")
    if not result.get("ses_basarili") or not audio or not Path(audio).exists():
        errors.append("Text-only pipeline tamamlandı ancak Autonoe ses dosyası üretilemedi.")
        edit_message(loading_id, _final_text_report(step_status, warnings, errors, result, tone_key))
        raise RuntimeError("Text-only pipeline tamamlandı ancak Autonoe ses dosyası üretilemedi.")

    caption = result.get("reels_aciklamasi") or ""
    hashtags = result.get("reels_hashtagleri") or []
    if not caption.strip():
        warnings.append("⚠️ Instagram açıklaması boş üretildi.")
    if not hashtags:
        warnings.append("⚠️ Hashtag listesi boş üretildi.")
    social_caption, caption_truncated = _caption_with_hashtags(caption, hashtags)
    if caption_truncated:
        warnings.append("⚠️ Telegram metin sınırı nedeniyle Instagram açıklaması kısaltıldı.")

    for i in range(len(TEXT_PIPELINE_STEPS)):
        step_status.setdefault(i, "🟢")
    edit_message(loading_id, _final_text_report(step_status, warnings, errors, result, tone_key))

    telegram_audio, cleanup_audio = _telegram_audio_path(audio)
    try:
        send_audio(telegram_audio, "🎧 Autonoe TTS — 1.20x")
    finally:
        if cleanup_audio:
            try:
                telegram_audio.unlink(missing_ok=True)
            except Exception:
                pass
    send_message(_format_title_options(result.get("kapak_basliklari") or []))
    threads = result.get("threads_aciklamasi") or ""
    social_bundle = "📝 INSTAGRAM AÇIKLAMASI + HASHTAGLER\n\n" + f"{social_caption}\n\n" + f"{threads if threads else 'Açıklama üretilemedi.'}"
    send_message(social_bundle)
    Path("pipeline_result.json").write_text(json.dumps({"mode": "text", "source": "telegram_text", "content_tone": tone_key, "input_text": text, "seslendirme": result.get("seslendirme_metni", ""), "audio": Path(audio).name, "caption": caption, "caption_telegram": social_caption, "title_options": result.get("kapak_basliklari", []), "threads": threads, "qa": result.get("qa_result", {}), "warnings": warnings, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    raw = os.environ.get("VIDEO_FILES", "").strip()
    inputs = [Path(line.strip()) for line in raw.splitlines() if line.strip()]
    text = os.environ.get("TEXT_INPUT", "").strip()
    if inputs and text:
        raise ValueError("Aynı çalıştırmada hem video hem text input verilemez.")
    if inputs:
        for path in inputs:
            if not path.exists():
                raise FileNotFoundError(f"Telegram intake output not found: {path}")
            print(f"Processing video: {path}", flush=True)
            process(path)
        return
    if text:
        print("Processing Telegram text-only input", flush=True)
        process_text(text)
        return
    raise ValueError("Telegram video veya text input bulunamadı.")


if __name__ == "__main__":
    main()
