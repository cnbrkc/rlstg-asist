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


def _format_title_options(titles):
    if not titles:
        return "🎯 BAŞLIK SEÇENEKLERİ\n\nBaşlık seçeneği üretilemedi."

    lines = ["🎯 BAŞLIK SEÇENEKLERİ", ""]
    for i, title in enumerate(titles, 1):
        if isinstance(title, dict):
            title = (
                title.get("title")
                or title.get("baslik")
                or title.get("başlık")
                or title.get("text")
                or title.get("metin")
                or str(title)
            )
        lines.append(f"{i}️⃣ {str(title).strip()}")
    return "\n".join(lines)


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
        secilen_ses_ingilizce="Autonoe",
        log_ekle=log,
        ilerlemeyi_guncelle=progress,
    )

    final = result.get("final_video")
    if not final or not Path(final).exists():
        raise RuntimeError("Pipeline tamamlandı ancak final video üretilemedi.")

    # 1. MESAJ: Video + Instagram/Facebook açıklaması + hemen altında hashtag'ler
    caption = result.get("reels_aciklamasi") or ""
    hashtags = result.get("reels_hashtagleri") or []
    if hashtags:
        caption += "\n\n" + " ".join("#" + str(x).lstrip("#") for x in hashtags)
    send_video(final, caption[:1024])

    # 2. MESAJ: Başlık seçenekleri
    send_message(_format_title_options(result.get("kapak_basliklari") or []))

    # 3. MESAJ: Threads açıklaması
    threads = result.get("threads_aciklamasi") or ""
    send_message(f"THREADS AÇIKLAMASI\n\n{threads}" if threads else "THREADS AÇIKLAMASI\n\nAçıklama üretilemedi.")

    summary = {
        "source": path.name,
        "final_video": Path(final).name,
        "seslendirme": result.get("seslendirme_metni", ""),
        "caption": caption,
        "title_options": result.get("kapak_basliklari", []),
        "threads": threads,
        "qa": result.get("qa_result", {}),
    }
    Path("pipeline_result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    raw = os.environ.get("VIDEO_FILES", "").strip()
    inputs = [Path(line.strip()) for line in raw.splitlines() if line.strip()]
    if not inputs:
        print("No new input videos from Telegram intake.")
        return
    for path in inputs:
        if not path.exists():
            raise FileNotFoundError(f"Telegram intake output not found: {path}")
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
