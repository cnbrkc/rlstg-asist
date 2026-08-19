import json
import mimetypes
import os
import sys
from pathlib import Path

# Add repository root to search path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from core.config import TON_DENGELI, TON_EGLENCE, TON_BILGI, TON_TEKNIK
from core.pipeline import pipeline_calistir, metin_pipeline_calistir
from core.router import SmartRouter
from core.media import video_suresini_al
from core.social_fallbacks import (
    first_fact as first_verified_fact, model_identity,
    text as _text,
)


def _token():
    return os.environ["TELEGRAM_BOT_TOKEN"]


def _chat_id():
    return os.environ["TELEGRAM_CHAT_ID"]


def _base():
    return f"https://api.telegram.org/bot{_token()}"


PIPELINE_STEPS = [
    "🎥 Forensic video analizi", "🔎 Research / Fact Lock", "🧠 Editorial Brain",
    "🎙️ Reels Creative + gerçek voice mode", "📝 Caption + Hashtag", "🧵 Threads", "🔍 QA + kontrollü regeneration",
    "🎧 TTS + gerçek süre doğrulaması", "🎬 FFmpeg video render",
]
TEXT_PIPELINE_STEPS = [
    "📝 Metin girdisi", "🔎 Research / Fact Lock", "🧠 Editorial Brain",
    "🎙️ Reels Creative + gerçek voice mode", "📝 Caption + Hashtag", "🧵 Threads", "🔍 QA + kontrollü regeneration",
    "🎧 TTS + gerçek süre doğrulaması",
]
TON_MAP = {"eglence": TON_EGLENCE, "dengeli": TON_DENGELI, "bilgi": TON_BILGI, "teknik": TON_TEKNIK}
TON_LABELS = {"eglence": "🎭 Eğlence Ağırlıklı", "dengeli": "⚖️ Dengeli", "bilgi": "🧠 Bilgi Ağırlıklı", "teknik": "📊 Teknik / Detaylı"}
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_VIDEO_CAPTION_LIMIT = 1024


def send_message(text):
    r = requests.post(f"{_base()}/sendMessage", data={"chat_id": _chat_id(), "text": text[:TELEGRAM_TEXT_LIMIT]}, timeout=60)
    r.raise_for_status()
    return r.json()


def edit_message(message_id, text):
    r = requests.post(f"{_base()}/editMessageText", data={"chat_id": _chat_id(), "message_id": message_id, "text": text[:TELEGRAM_TEXT_LIMIT]}, timeout=60)
    r.raise_for_status()


def send_video(path, caption):
    with open(path, "rb") as fh:
        r = requests.post(f"{_base()}/sendVideo", data={"chat_id": _chat_id(), "caption": caption[:TELEGRAM_VIDEO_CAPTION_LIMIT]}, files={"video": (Path(path).name, fh, "video/mp4")}, timeout=300)
    r.raise_for_status()


def video_duration(path):
    # Doğrudan ffprobe çağırmıyoruz: GitHub Actions ortamında ffprobe kurulmuyor
    # (ffmpeg yalnızca imageio-ffmpeg'den geliyor). core.media.video_suresini_al
    # ffprobe varsa onu, yoksa ffmpeg -i Duration çıktısını kullanır; böylece
    # süre her ortamda güvenle okunur. Pipeline iç düşüşüyle aynı mantık.
    try:
        sure = video_suresini_al(str(path))
        return sure if sure and sure > 0 else None
    except Exception:
        return None


def _format_title_options(titles):
    if not titles:
        return "🎯 BAŞLIK SEÇENEKLERİ\n\nBaşlık seçeneği üretilemedi."
    lines = ["🎯 BAŞLIK SEÇENEKLERİ", ""]
    for i, item in enumerate(titles, 1):
        if isinstance(item, dict):
            ana = str(item.get("ana", "")).strip()
            alt = str(item.get("alt", "")).strip()
            lines.append(f"{i}️⃣ {ana}")
            if alt:
                lines.append(f"   ↳ {alt}")
        else:
            lines.append(f"{i}️⃣ {str(item).strip()}")
    return "\n".join(lines)


def _loading_text(done, current=None, warnings=0, errors=0, steps=None):
    steps = steps or PIPELINE_STEPS
    total = len(steps)
    filled = min(done, total)
    bar = "█" * filled + "░" * (total - filled)
    lines = ["⏳ REELS PIPELINE", "", f"{bar}  {done}/{total}"]
    if current:
        lines += ["", f"🔄 {current}"]
    if warnings or errors:
        lines += ["", f"⚠️ Uyarı: {warnings}   ❌ Hata: {errors}"]
    return "\n".join(lines)


def _extract_media_lines(warnings):
    return [x for x in warnings if x.startswith("📐 ") or x.startswith("🎚️ ")]


def _final_report(step_status, warnings, errors, result, tone_key):
    lines = ["📊 PIPELINE RAPORU", ""]
    for i, name in enumerate(PIPELINE_STEPS):
        lines.append(f"{step_status.get(i, '⚪')} {i+1}/9 {name}")
    input_media = result.get("input_media") or {}
    output_media = result.get("output_media") or {}
    lines += [
        "",
        f"🎯 İçerik tonu: {TON_LABELS.get(tone_key, tone_key)}",
        f"🗣️ Gerçek voice mode: {result.get('ses_modu') or 'Bilinmiyor'}",
        f"🎙️ Gerçek TTS sesi: {result.get('ses_modu_sesi') or result.get('kullanilan_ses_modeli') or result.get('secilen_ses_ingilizce') or 'Bilinmiyor'}",
        "⚡ TTS hız: 1.20x",
        f"🔁 QA regeneration: {result.get('qa_regeneration_rounds', 0)} / 1",
        f"✅ QA final: {'PASS' if result.get('qa_pass') else 'FAIL'}",
        f"🎚️ Senkron: {result.get('sync_note') or 'TTS gerçek WAV süresi doğrulandı'}",
    ]
    if input_media:
        lines.append(f"📥 Input: {input_media.get('width','?')}x{input_media.get('height','?')} | {input_media.get('fps',0):.3f} FPS | {input_media.get('duration',0):.2f}s")
    if output_media:
        lines.append(f"📤 Output: {output_media.get('width','?')}x{output_media.get('height','?')} | {output_media.get('fps',0):.3f} FPS | {output_media.get('duration',0):.2f}s")
    lines += [f"⚠️ Uyarı: {len(warnings)}", f"❌ Hata: {len(errors)}"]
    media_lines = _extract_media_lines(warnings)
    if media_lines:
        lines += ["", "🎥 MEDYA TEŞHİSİ"] + media_lines
    if warnings:
        lines += ["", "⚠️ UYARILAR"] + [f"• {x}" for x in warnings[:8]]
    if errors:
        lines += ["", "❌ HATALAR"] + [f"• {x}" for x in errors[:8]]
    return "\n".join(lines)[:TELEGRAM_TEXT_LIMIT]


def _qa_text(qa_result):
    if not qa_result:
        return "QA sonucu bulunamadı."
    try:
        return json.dumps(qa_result, ensure_ascii=False, indent=2)
    except Exception:
        return str(qa_result)


def _social_fallbacks(result):
    """result dict'inden güvenli caption/hashtags/threads üretir (model boş/artifact döndüğünde kullanılır)."""
    result = result if isinstance(result, dict) else {}
    editorial = result.get("editorial_brief") if isinstance(result.get("editorial_brief"), dict) else {}
    fact_state = result.get("fact_lock") if isinstance(result.get("fact_lock"), dict) else {}
    video_state = (result.get("pipeline_state") or {}).get("video_state", {}) if isinstance(result.get("pipeline_state"), dict) else {}
    identity = model_identity(video_state) or "bu araç"
    core = _text(editorial.get("core_story"))
    discussion = _text(editorial.get("discussion_territory"))
    fact = first_verified_fact(fact_state)
    caption = "\n\n".join(x for x in [
        f"{identity}: videonun ötesinde asıl merak edilen taraf burada başlıyor.",
        core or fact or "Videodaki detayları Fact Lock sınırları içinde değerlendiriyoruz.",
        fact,
        "Rakamlar kadar gerçek kullanımın ne söylediği de önemli.",
    ] if x)[:900].rstrip()
    threads = (discussion or core or fact or f"{identity} tarafında asıl tartışma, görünen detayın gerçek kullanımda ne ifade ettiği.")[:480].rstrip()
    hashtags = ["otoxtra", "otomobil", "araba", "otomobilhaber", "arabasever"]
    return caption, hashtags, threads


def _caption_with_hashtags(description, hashtags):
    desc = str(description or "").strip()
    tags = " ".join("#" + str(x).lstrip("#").strip() for x in (hashtags or []) if str(x).strip())
    if not tags:
        return desc[:TELEGRAM_VIDEO_CAPTION_LIMIT], bool(desc) and len(desc) > TELEGRAM_VIDEO_CAPTION_LIMIT
    suffix = "\n\n" + tags
    if len(suffix) >= TELEGRAM_VIDEO_CAPTION_LIMIT:
        return suffix[-TELEGRAM_VIDEO_CAPTION_LIMIT:], bool(desc)
    available = TELEGRAM_VIDEO_CAPTION_LIMIT - len(suffix)
    truncated = len(desc) > available
    return desc[:available].rstrip() + suffix, truncated


def _threads_message(threads):
    return str(threads or '').strip()


def _setup_env(steps, loading_id):
    """Ortam değişkenlerinden tone kurar ve log/progress callback'leri ile durum torbalarını hazırlar."""
    step_status, warnings, errors = {}, [], []
    tone_key = os.environ.get("CONTENT_TONE", "dengeli").strip().lower()
    tone_key = tone_key if tone_key in TON_MAP else "dengeli"
    selected_tone = TON_MAP[tone_key]
    capture_diagnostics = steps is PIPELINE_STEPS  # video path medya teşhisi satırlarını warnings'e ekler

    def log(msg):
        text = str(msg).strip()
        print(text, flush=True)
        lower = text.lower()
        if "⚠️" in text or "uyarı" in lower or "warning" in lower:
            warnings.append(text)
        if "❌" in text or "hata" in lower or "error" in lower:
            errors.append(text)
        if capture_diagnostics and (text.startswith("📐 ") or text.startswith("🎚️ ")):
            warnings.append(text)

    def progress(n, total, msg):
        done = max(0, min(n - 1, len(steps)))
        if done > 0:
            step_status[done - 1] = "🟢"
        current = steps[n - 1] if 0 < n <= len(steps) else str(msg)
        try:
            edit_message(loading_id, _loading_text(done, current, len(warnings), len(errors), steps=steps))
        except Exception as exc:
            print(f"Loading mesajı güncellenemedi: {exc}", flush=True)

    return step_status, warnings, errors, tone_key, selected_tone, log, progress


def _ensure_social_outputs(result, warnings):
    """Caption/hashtags/threads boş veya artifact ise Fact Lock tabanlı fallback uygular."""
    caption = _text(result.get("reels_aciklamasi"))
    hashtags = result.get("reels_hashtagleri") or []
    if not caption or not hashtags:
        fallback_caption, fallback_hashtags, fallback_threads = _social_fallbacks(result)
        if not caption:
            caption = fallback_caption
            result["reels_aciklamasi"] = caption
            warnings.append("⚠️ Caption modeli boş döndü; Fact Lock tabanlı güvenli sosyal fallback kullanıldı.")
        if not hashtags:
            hashtags = fallback_hashtags
            result["reels_hashtagleri"] = hashtags
            warnings.append("⚠️ Hashtag modeli boş döndü; güvenli varsayılan hashtag seti kullanıldı.")
    threads = _text(result.get("threads_aciklamasi"))
    if not threads:
        _, _, fallback_threads = _social_fallbacks(result)
        threads = fallback_threads
        result["threads_aciklamasi"] = threads
        warnings.append("⚠️ Threads modeli boş döndü; Fact Lock tabanlı güvenli fallback kullanıldı.")
    return caption, hashtags, threads


def process(path):
    initial = send_message(_loading_text(0, "Video alındı, pipeline başlatılıyor..."))
    loading_id = initial["result"]["message_id"]
    raw = path.read_bytes()
    duration = video_duration(path)
    mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    router = SmartRouter()
    step_status, warnings, errors, tone_key, selected_tone, log, progress = _setup_env(PIPELINE_STEPS, loading_id)
    user_video_note = os.environ.get("VIDEO_ANALYSIS_NOTE", "").strip()
    video_note = f"KULLANICI TELEGRAM NOTU (MUTLAK ÖNCELİKLİ):\n{user_video_note}" if user_video_note else ""

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

    result["sync_note"] = next((x for x in warnings if "senkron" in x.lower() or "süre uyumu" in x.lower()), "TTS gerçek WAV süresi doğrulandı")
    caption, hashtags, threads = _ensure_social_outputs(result, warnings)
    video_caption, caption_truncated = _caption_with_hashtags(caption, hashtags)
    if caption_truncated:
        warnings.append(f"⚠️ Telegram video caption sınırı ({TELEGRAM_VIDEO_CAPTION_LIMIT} karakter): açıklama kısaltıldı; tam Instagram metni aşağıdaki sosyal çıktıda korunuyor.")
    for i in range(len(PIPELINE_STEPS)):
        step_status.setdefault(i, "🟢")
    edit_message(loading_id, _final_report(step_status, warnings, errors, result, tone_key))
    send_video(final, video_caption)
    send_message(_format_title_options(result.get("kapak_basliklari") or []))
    if threads:
        send_message(_threads_message(threads))
    Path("pipeline_result.json").write_text(json.dumps({"source": path.name, "final_video": Path(final).name, "content_tone": tone_key, "video_note": user_video_note, "seslendirme": result.get("seslendirme_metni", ""), "caption": caption, "caption_telegram": video_caption, "title_options": result.get("kapak_basliklari", []), "threads": threads, "qa": result.get("qa_result", {}), "qa_pass": result.get("qa_pass"), "qa_regeneration_rounds": result.get("qa_regeneration_rounds", 0), "voice_mode": result.get("ses_modu"), "voice": result.get("ses_modu_sesi"), "input_media": result.get("input_media", {}), "output_media": result.get("output_media", {}), "warnings": warnings, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")


def process_text(text):
    text = (text or "").strip()
    initial = send_message(_loading_text(0, "Metin alındı, text-only pipeline başlatılıyor...", steps=TEXT_PIPELINE_STEPS))
    loading_id = initial["result"]["message_id"]
    router = SmartRouter()
    step_status, warnings, errors, tone_key, selected_tone, log, progress = _setup_env(TEXT_PIPELINE_STEPS, loading_id)

    try:
        result = metin_pipeline_calistir(router=router, metin=text, icerik_tonu=selected_tone, secilen_ses_ingilizce="Autonoe", log_ekle=log, ilerlemeyi_guncelle=progress)
    except Exception as exc:
        errors.append(str(exc))
        edit_message(loading_id, _final_report(step_status, warnings, errors, {"secilen_ses_ingilizce": "Autonoe"}, tone_key))
        raise

    caption, hashtags, threads = _ensure_social_outputs(result, warnings)
    for i in range(len(TEXT_PIPELINE_STEPS)):
        step_status.setdefault(i, "🟢")
    edit_message(loading_id, _final_report(step_status, warnings, errors, result, tone_key))
    if threads:
        send_message(_threads_message(threads))
    Path("pipeline_result.json").write_text(json.dumps({"source": "text", "content_tone": tone_key, "caption": caption, "title_options": result.get("kapak_basliklari", []), "threads": threads, "qa": result.get("qa_result", {}), "qa_pass": result.get("qa_pass"), "warnings": warnings, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    video_files = [Path(x.strip()) for x in os.environ.get("VIDEO_FILES", "").split(",") if x.strip()]
    text_input = os.environ.get("TEXT_INPUT", "").strip()
    if video_files:
        for path in video_files:
            print(f"Processing video: {path}", flush=True)
            process(path)
    elif text_input:
        process_text(text_input)
    else:
        raise RuntimeError("VIDEO_FILES veya TEXT_INPUT bulunamadı.")


if __name__ == "__main__":
    main()
