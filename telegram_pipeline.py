import os, sys, json, mimetypes, tempfile, subprocess
from pathlib import Path
import requests

from router import SmartRouter
from pipeline import pipeline_calistir
from config import TON_DENGELI, MAX_VIDEO_BOYUT, MIN_SURE_SANIYE, MAX_SURE_SANIYE

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"


def tg(method, **data):
    r = requests.post(f"{API}/{method}", data=data, timeout=60)
    r.raise_for_status()
    out = r.json()
    if not out.get("ok"):
        raise RuntimeError(out)
    return out["result"]


def send(chat_id, text):
    tg("sendMessage", chat_id=chat_id, text=text)


def send_file(chat_id, path, caption=None):
    with open(path, "rb") as f:
        r = requests.post(f"{API}/sendVideo", data={"chat_id": chat_id, "supports_streaming": "true", "caption": caption or ""}, files={"video": (Path(path).name, f, "video/mp4")}, timeout=180)
    r.raise_for_status()
    out = r.json()
    if not out.get("ok"):
        raise RuntimeError(out)


def latest_video_update():
    data = tg("getUpdates", timeout=0, allowed_updates=json.dumps(["message"]))
    for u in reversed(data):
        m = u.get("message") or {}
        if m.get("video") or m.get("document", {}).get("mime_type", "").startswith("video/"):
            return u
    return None


def duration(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path], capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except Exception:
        return 30.0


def mime_for(path):
    return mimetypes.guess_type(path)[0] or "video/mp4"


def main():
    update = latest_video_update()
    if not update:
        raise SystemExit("Telegram'da işlenecek yeni video bulunamadı.")
    msg = update["message"]
    chat_id = msg["chat"]["id"]
    video = msg.get("video") or msg.get("document")
    file_id = video["file_id"]
    size = int(video.get("file_size") or 0)
    if size and size > MAX_VIDEO_BOYUT:
        send(chat_id, f"❌ Video çok büyük: {size/1024/1024:.1f} MB. Bu bot sürümünde üst sınır 50 MB; Telegram Bot API indirme sınırı nedeniyle pratikte 20 MB altında gönder.")
        raise SystemExit(1)

    send(chat_id, "📥 Video alındı. Reels pipeline başlıyor...")
    info = tg("getFile", file_id=file_id)
    file_path = info["file_path"]
    raw = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}", timeout=180)
    raw.raise_for_status()

    suffix = Path(file_path).suffix or ".mp4"
    with tempfile.TemporaryDirectory() as td:
        input_path = os.path.join(td, "input" + suffix)
        with open(input_path, "wb") as f:
            f.write(raw.content)
        video_bytes = raw.content
        sure = duration(input_path)
        if not (MIN_SURE_SANIYE <= sure <= MAX_SURE_SANIYE):
            send(chat_id, f"❌ Video süresi uygun değil: {sure:.1f} sn. Sınır {MIN_SURE_SANIYE}-{MAX_SURE_SANIYE} sn.")
            raise SystemExit(1)

        logs = []
        last_progress = {"n": 0}
        def log(msg):
            print(msg, flush=True)
            logs.append(msg)
        def progress(n, total, text):
            if n != last_progress["n"]:
                last_progress["n"] = n
                send(chat_id, f"{n}/{total}  {text}")

        router = SmartRouter()
        try:
            result = pipeline_calistir(
                router=router,
                video_bytes=video_bytes,
                mime_type=mime_for(input_path),
                temp_input_video=input_path,
                video_analiz_notlari="",
                metin_uretim_notlari="",
                sure_saniye=int(round(sure)),
                icerik_tonu=TON_DENGELI,
                secilen_ses_ingilizce="Autonoe",
                log_ekle=log,
                ilerlemeyi_guncelle=progress,
            )
        except Exception as e:
            send(chat_id, "❌ Pipeline hata verdi:\n\n" + str(e)[:2500])
            raise

        text = (
            "✅ PIPELINE TAMAMLANDI\n\n"
            f"🎙️ Voice-over:\n{result.get('seslendirme_metni','')}\n\n"
            f"📝 Caption:\n{result.get('reels_aciklamasi','')}\n\n"
            f"#️⃣ Hashtag:\n{' '.join(result.get('reels_hashtagleri',[]))}\n\n"
            f"🧵 Threads:\n{result.get('threads_aciklamasi','')}\n\n"
            f"🖼️ Kapak:\n{' | '.join(result.get('kapak_basliklari',[]))}"
        )
        send(chat_id, text[:3900])
        final_video = result.get("final_video", "")
        if final_video and os.path.exists(final_video):
            send_file(chat_id, final_video, "🎬 Final Reels hazır")
        else:
            send(chat_id, "⚠️ Metin/TTS tamamlandı fakat final video oluşturulamadı. Actions loguna bakacağız.")


if __name__ == "__main__":
    main()
