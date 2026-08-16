import json
import mimetypes
import os
import subprocess
from pathlib import Path

import requests

from config import TON_DENGELI, TON_EGLENCE, TON_BILGI, TON_TEKNIK
from pipeline import pipeline_calistir, metin_pipeline_calistir
from router import SmartRouter

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
BASE = f"https://api.telegram.org/bot{TOKEN}"

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
TELEGRAM_AUDIO_CAPTION_LIMIT = 1024


def send_message(text):
    r = requests.post(f"{BASE}/sendMessage", data={"chat_id": CHAT_ID, "text": text[:TELEGRAM_TEXT_LIMIT]}, timeout=60)
    r.raise_for_status()
    return r.json()


def edit_message(message_id, text):
    r = requests.post(f"{BASE}/editMessageText", data={"chat_id": CHAT_ID, "message_id": message_id, "text": text[:TELEGRAM_TEXT_LIMIT]}, timeout=60)
    r.raise_for_status()


def send_video(path, caption):
    with open(path, "rb") as fh:
        r = requests.post(f"{BASE}/sendVideo", data={"chat_id": CHAT_ID, "caption": caption[:TELEGRAM_VIDEO_CAPTION_LIMIT]}, files={"video": (Path(path).name, fh, "video/mp4")}, timeout=300)
    r.raise_for_status()


def send_audio(path, caption=""):
    with open(path, "rb") as fh:
        r = requests.post(f"{BASE}/sendAudio", data={"chat_id": CHAT_ID, "caption": caption[:TELEGRAM_AUDIO_CAPTION_LIMIT]}, files={"audio": (Path(path).name, fh, "audio/mpeg")}, timeout=300)
    r.raise_for_status()


def _telegram_audio_path(source):
    source = Path(source)
    if source.suffix.lower() in {".mp3", ".m4a"}:
        return source, False
    target = source.with_name(source.stem + ".telegram.mp3")
    subprocess.run(["ffmpeg", "-y", "-i", str(source), "-codec:a", "libmp3lame", "-q:a", "2", str(target)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=120)
    return target, True


def video_duration(path):
    try:
        out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], text=True, timeout=30)
        return float(out.strip())
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
        f"🔁 QA regeneration: {result.get('qa_regeneration_rounds', 0)} / 2",
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


def _final_text_report(step_status, warnings, errors, result, tone_key):
    lines = ["📊 TEXT-ONLY PIPELINE RAPORU", ""]
    for i, name in enumerate(TEXT_PIPELINE_STEPS):
        lines.append(f"{step_status.get(i, '⚪')} {i+1}/8 {name}")
    lines += ["", f"🎯 İçerik tonu: {TON_LABELS.get(tone_key, tone_key)}", f"🗣️ Gerçek voice mode: {result.get('ses_modu') or 'Bilinmiyor'}", f"🎙️ Gerçek TTS sesi: {result.get('ses_modu_sesi') or result.get('kullanilan_ses_modeli') or 'Bilinmiyor'}", "⚡ TTS hız: 1.20x", f"🔁 QA regeneration: {result.get('qa_regeneration_rounds', 0)} / 2", f"✅ QA final: {'PASS' if result.get('qa_pass') else 'FAIL'}", "🎬 Video render: atlandı (text-only)", f"⚠️ Uyarı: {len(warnings)}", f"❌ Hata: {len(errors)}", "", "🔍 QA SONUCU", _qa_text(result.get("qa_result"))[:2200]]
    if warnings:
        lines += ["", "⚠️ UYARILAR"] + [f"• {x}" for x in warnings[:6]]
    if errors:
        lines += ["", "❌ HATALAR"] + [f"• {x}" for x in errors[:6]]
    return "\n".join(lines)[:TELEGRAM_TEXT_LIMIT]


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


def process(path):
    initial = send_message(_loading_text(0, "Video alındı, pipeline başlatılıyor..."))
    loading_id = initial["result"]["message_id"]
    raw = path.read_bytes()
    duration = video_duration(path)
    mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    router = SmartRouter()
    step_status = {}
    warnings = []
    errors = []
    tone_key = os.environ.get("CONTENT_TONE", "dengeli").strip().lower()
    tone_key = tone_key if tone_key in TON_MAP else "dengeli"
    selected_tone = TON_MAP[tone_key]
    user_video_note = os.environ.get("VIDEO_ANALYSIS_NOTE", "").strip()
    # Telegram video caption'ı zaten kullanıcının analiz notudur. Ek OCR/Tesseract
    # taraması yapılmaz; bu hem gereksiz gecikmeyi hem de yanlış OCR çıkarımlarını önler.
    video_note = f"KULLANICI TELEGRAM NOTU (MUTLAK ÖNCELİKLİ):\n{user_video_note}" if user_video_note else ""

    def log(msg):
        text = str(msg).strip()
        print(text, flush=True)
        lower = text.lower()
        if "⚠️" in text or "uyarı" in lower or "warning" in lower:
            warnings.append(text)
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

    result["sync_note"] = next((x for x in warnings if "senkron" in x.lower() or "süre uyumu" in x.lower()), "TTS gerçek WAV süresi doğrulandı")
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
    Path("pipeline_result.json").write_text(json.dumps({"source": path.name, "final_video": Path(final).name, "content_tone": tone_key, "video_note": user_video_note, "seslendirme": result.get("seslendirme_metni", ""), "caption": caption, "caption_telegram": video_caption, "title_options": result.get("kapak_basliklari", []), "threads": threads, "qa": result.get("qa_result", {}), "qa_pass": result.get("qa_pass"), "qa_regeneration_rounds": result.get("qa_regeneration_rounds", 0), "voice_mode": result.get("ses_modu"), "voice": result.get("ses_modu_sesi"), "input_media": result.get("input_media", {}), "output_media": result.get("output_media", {}), "warnings": warnings, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")


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
        errors.append("Text-only pipeline tamamlandı ancak QA PASS sonrası kullanılabilir TTS dosyası üretilemedi.")
        edit_message(loading_id, _final_text_report(step_status, warnings, errors, result, tone_key))
        raise RuntimeError("Text-only pipeline tamamlandı ancak kullanılabilir TTS dosyası üretilemedi.")

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
        send_audio(telegram_audio, f"🎧 {result.get('ses_modu_sesi') or result.get('ses_modu') or 'TTS'} — 1.20x")
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
    Path("pipeline_result.json").write_text(json.dumps({"mode": "text", "source": "telegram_text", "content_tone": tone_key, "input_text": text, "seslendirme": result.get("seslendirme_metni", ""), "audio": Path(audio).name, "caption": caption, "caption_telegram": social_caption, "title_options": result.get("kapak_basliklari", []), "threads": threads, "qa": result.get("qa_result", {}), "qa_pass": result.get("qa_pass"), "qa_regeneration_rounds": result.get("qa_regeneration_rounds", 0), "voice_mode": result.get("ses_modu"), "voice": result.get("ses_modu_sesi"), "warnings": warnings, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")


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
