import json
import os
from pathlib import Path

import requests

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
BASE = f"https://api.telegram.org/bot{TOKEN}"
LIMIT = 4096


def send_message(text):
    response = requests.post(
        f"{BASE}/sendMessage",
        data={"chat_id": CHAT_ID, "text": str(text)[:LIMIT]},
        timeout=60,
    )
    response.raise_for_status()


def _fallback_caption(data):
    fact_lock = data.get("fact_lock") if isinstance(data, dict) else {}
    editorial = data.get("editorial_brief") if isinstance(data, dict) else {}
    facts = fact_lock.get("facts") if isinstance(fact_lock, dict) else []
    fact = ""
    if isinstance(facts, list):
        for item in facts:
            if isinstance(item, dict) and str(item.get("status") or "").upper() in {"OBSERVED", "VERIFIED"}:
                fact = str(item.get("fact") or "").strip()
                if fact:
                    break
    core = str(editorial.get("core_story") or "").strip() if isinstance(editorial, dict) else ""
    model = "bu araç"
    pipeline_state = data.get("pipeline_state") if isinstance(data, dict) else {}
    video_state = pipeline_state.get("video_state") if isinstance(pipeline_state, dict) else {}
    identity = video_state.get("video_identity") if isinstance(video_state, dict) else {}
    if isinstance(identity, dict):
        model = str(identity.get("exact_model") or identity.get("brand") or "bu araç").strip() or "bu araç"
    if model.upper() == "UNKNOWN":
        model = "bu araç"
    return "\n\n".join(
        x for x in [
            f"{model}: videonun ötesinde asıl merak edilen taraf burada başlıyor.",
            core or fact or "Videodaki detayları Fact Lock sınırları içinde değerlendiriyoruz.",
            fact,
            "Rakamlar kadar gerçek kullanımın ne söylediği de önemli.",
        ] if x
    )[:900].rstrip()


def main():
    result_path = Path("pipeline_result.json")
    if not result_path.exists():
        raise FileNotFoundError("pipeline_result.json bulunamadı; sosyal çıktı gönderilemez.")

    data = json.loads(result_path.read_text(encoding="utf-8"))
    caption = str(data.get("caption") or data.get("reels_aciklamasi") or "").strip()
    hashtags = data.get("reels_hashtagleri") or []
    threads = str(data.get("threads") or data.get("threads_aciklamasi") or "").strip()

    # A WAV path, an empty value, or an implausibly short caption is never a valid
    # Instagram description. Keep the existing model output when it is usable;
    # otherwise use the same Fact Lock-safe fallback family as the worker.
    suspicious_path = caption.startswith("/tmp/") or caption.lower().endswith((".wav", ".mp3"))
    if not caption or suspicious_path:
        caption = _fallback_caption(data)

    tag_text = " ".join(
        "#" + str(tag).lstrip("#").strip()
        for tag in hashtags
        if str(tag).strip()
    )
    if not tag_text:
        tag_text = "#otoxtra #otomobil #araba #otomobilhaber #arabasever"

    social_text = f"📝 INSTAGRAM AÇIKLAMASI + HASHTAGLER\n\n{caption}\n\n{tag_text}"
    send_message(social_text)

    if threads:
        send_message(f"🧵 THREADS AÇIKLAMASI ({len(threads)} karakter)\n\n{threads}")
    else:
        send_message("🧵 THREADS AÇIKLAMASI (0 karakter)\n\n⚠️ Threads açıklaması boş üretildi.")

    print(f"SOCIAL OUTPUT CHECK: caption={len(caption)} chars | hashtags={len(hashtags)} | threads={len(threads)} chars", flush=True)


if __name__ == "__main__":
    main()
