import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE = f"https://api.telegram.org/bot{TOKEN}"
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
OFFSET_FILE = DATA_DIR / "telegram_offset.txt"


def api(method, params=None):
    params = params or {}
    encoded = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{BASE}/{method}", data=encoded)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode())


def send_message(chat_id, text):
    api("sendMessage", {"chat_id": chat_id, "text": text})


def download_file(file_id, destination):
    result = api("getFile", {"file_id": file_id})
    if not result.get("ok"):
        raise RuntimeError(f"getFile failed: {result}")
    file_path = result["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    urllib.request.urlretrieve(url, destination)


def export_new_videos(paths):
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as fh:
        fh.write("new_videos<<EOF\n")
        for path in paths:
            fh.write(f"{path}\n")
        fh.write("EOF\n")


def main():
    offset = int(OFFSET_FILE.read_text().strip()) if OFFSET_FILE.exists() else None
    params = {"timeout": 5, "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        params["offset"] = offset

    result = api("getUpdates", params)
    if not result.get("ok"):
        raise RuntimeError(result)

    updates = result.get("result", [])
    if not updates:
        print("No new Telegram messages.")
        export_new_videos([])
        return

    new_videos = []

    for update in updates:
        update_id = update["update_id"]
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            OFFSET_FILE.write_text(str(update_id + 1))
            continue

        video = message.get("video")
        document = message.get("document")

        try:
            if video:
                file_id = video["file_id"]
                filename = f"telegram_{update_id}.mp4"
                destination = DATA_DIR / filename
                send_message(chat_id, "📥 Videonu aldım. İndirmeyi başlatıyorum...")
                download_file(file_id, destination)
                new_videos.append(str(destination))
                size_mb = destination.stat().st_size / (1024 * 1024)
                send_message(chat_id, f"✅ Video GitHub Actions'a ulaştı.\n\n📁 {filename}\n📦 {size_mb:.1f} MB\n\nPipeline başlatılıyor...")
            elif document and (document.get("mime_type") or "").startswith("video/"):
                file_id = document["file_id"]
                filename = document.get("file_name") or f"telegram_{update_id}.mp4"
                destination = DATA_DIR / Path(filename).name
                send_message(chat_id, "📥 Video dosyasını aldım. İndirmeyi başlatıyorum...")
                download_file(file_id, destination)
                new_videos.append(str(destination))
                size_mb = destination.stat().st_size / (1024 * 1024)
                send_message(chat_id, f"✅ Video GitHub Actions'a ulaştı.\n\n📁 {destination.name}\n📦 {size_mb:.1f} MB\n\nPipeline başlatılıyor...")
            elif message.get("text") == "/start":
                send_message(chat_id, "🤖 Reels Asistanı hazır.\n\nBana bir video gönder.")
            elif message.get("text"):
                send_message(chat_id, "🎥 Şimdilik bana bir video gönder.")
        except Exception as exc:
            send_message(chat_id, f"❌ Videoyu işlerken hata oluştu: {exc}")

        OFFSET_FILE.write_text(str(update_id + 1))

    export_new_videos(new_videos)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Worker error: {exc}", file=sys.stderr)
        raise
