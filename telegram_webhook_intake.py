import os
import sys
import urllib.request
import urllib.parse
from pathlib import Path

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
FILE_ID = os.environ["TELEGRAM_FILE_ID"]
FILENAME = Path(os.environ.get("TELEGRAM_FILENAME", "telegram_video.mp4")).name
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
DESTINATION = DATA_DIR / FILENAME
BASE = f"https://api.telegram.org/bot{TOKEN}"


def api(method, params=None):
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(f"{BASE}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=60) as response:
        import json
        return json.loads(response.read().decode())


def send(text):
    result = api("sendMessage", {"chat_id": CHAT_ID, "text": text})
    if not result.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {result}")


def main():
    send(f"📥 Video alındı.\n\n📁 {FILENAME}\n\n🚀 Reels pipeline başlıyor...")
    result = api("getFile", {"file_id": FILE_ID})
    if not result.get("ok"):
        raise RuntimeError(f"getFile failed: {result}")
    file_path = result["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    urllib.request.urlretrieve(url, DESTINATION)
    size_mb = DESTINATION.stat().st_size / (1024 * 1024)
    send(f"✅ Video indirildi.\n\n📁 {FILENAME}\n📦 {size_mb:.1f} MB\n\nPipeline devam ediyor...")
    print(f"VIDEO_PATH={DESTINATION}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try:
            send(f"❌ Video alınırken hata oluştu:\n\n{str(exc)[:2000]}")
        except Exception:
            pass
        print(f"Webhook intake error: {exc}", file=sys.stderr)
        raise
