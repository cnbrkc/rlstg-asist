import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# Add repository root to search path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _token():
    return os.environ["TELEGRAM_BOT_TOKEN"]


def _chat_id():
    return os.environ["TELEGRAM_CHAT_ID"]


def _file_id():
    return os.environ["TELEGRAM_FILE_ID"]


def _base():
    return f"https://api.telegram.org/bot{_token()}"


def api(method, params=None):
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(f"{_base()}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=60) as response:
        import json
        return json.loads(response.read().decode())


def send(text):
    result = api("sendMessage", {"chat_id": _chat_id(), "text": text})
    if not result.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {result}")


def _safe_filename(value):
    basename = Path(str(value or "telegram_video.mp4").replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._")
    return (cleaned or "telegram_video.mp4")[:160]


def main():
    filename = _safe_filename(os.environ.get("TELEGRAM_FILENAME"))
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    destination = data_dir / filename
    send(f"📥 Video alındı.\n\n📁 {filename}\n\n🚀 Reels pipeline başlıyor...")
    result = api("getFile", {"file_id": _file_id()})
    if not result.get("ok"):
        raise RuntimeError(f"getFile failed: {result}")
    file_path = result["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{_token()}/{file_path}"
    urllib.request.urlretrieve(url, destination)
    size_mb = destination.stat().st_size / (1024 * 1024)
    send(f"✅ Video indirildi.\n\n📁 {filename}\n📦 {size_mb:.1f} MB\n\nPipeline devam ediyor...")
    github_env = os.environ.get("GITHUB_ENV", "").strip()
    if github_env:
        with open(github_env, "a", encoding="utf-8") as env_file:
            env_file.write(f"VIDEO_FILES={destination.as_posix()}\n")
    print(f"VIDEO_PATH={destination}")


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
