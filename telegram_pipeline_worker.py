import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path

import requests

from config import TON_DENGELI
from pipeline import pipeline_calistir
from router import SmartRouter

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
BASE = f"https://api.telegram.org/bot{TOKEN}"


def send_message(text):
    r = requests.post(f"{BASE}/sendMessage", data={"chat_id": CHAT_ID, "text": text}, timeout=60)
    r.raise_for_status()


def send_video(path, caption):
    with open(path, "rb") as fh:
        r = requests.post(
            f"{BASE}/sendVideo",
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"video": (Path(path).name, fh, "video/mp4")},
            timeout=300,
        )
    r.raise_for_status()


def latest_inputs():
    return sorted(Path("data").glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)


def video_duration(path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            text=True,
            timeout=30,
        )
        return float(out.strip())
    except Exception:
        return None


def process(path):
    send_message(f"📥 Video alındı. Reels pipeline başlıyor...\n\n📁 {path.name}")
    raw = path.read_bytes()
    duration = video_duration(path)
    mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    router = SmartRouter()

    def log(msg):
        send_message(msg)

    def progress(n, total, msg):
        send_message(f"{n}/{total}  {msg}")

    result = pipeline_calistir(
        router=router,
        video_bytes=raw,
        mime_type=mime,
        temp_input_video=str(path),
        video_analiz_notlari="",
        metin_uretim_notlari="",
        sure_saniye=duration,
        icerik_tonu=TON_DENGELI,
        secilen_ses_ingilizce="Puck",
        log_ekle=log,
        ilerlemeyi_guncelle=progress,
    )

    final = result.get("final_video")
    if not final or not Path(final).exists():
        raise RuntimeError("Pipeline tamamlandı ancak final video üretilemedi.")

    caption = result.get("reels_aciklamasi") or ""
    hashtags = result.get("reels_hashtagleri") or []
    if hashtags:
        caption += "\n\n" + " ".join("#" + str(x).lstrip("#") for x in hashtags)

    send_message("✅ Pipeline tamamlandı. Final video Telegram'a gönderiliyor...")
    send_video(final, caption[:1024])

    summary = {
        "source": path.name,
        "final_video": Path(final).name,
        "seslendirme": result.get("seslendirme_metni", ""),
        "caption": caption,
        "threads": result.get("threads_aciklamasi", ""),
        "qa": result.get("qa_result", {}),
    }
    Path("pipeline_result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    send_message("📝 Caption ve Threads çıktıları hazır. Detaylar Actions logunda; final video yukarıda.")


def main():
    inputs = latest_inputs()
    if not inputs:
        print("No input videos in data/.")
        return
    for path in inputs:
        print(f"Processing {path}")
        process(path)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Pipeline worker error: {exc}", file=sys.stderr)
        try:
            send_message(f"❌ Pipeline hata verdi:\n\n{str(exc)[:3500]}")
        finally:
            raise
