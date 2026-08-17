"""Single Telegram social-delivery entrypoint.

Keeps the existing guard/worker pipeline intact, but exposes the generated
Instagram caption + hashtags together with the single Threads output in one
Telegram message. The video caption remains unchanged. This avoids the old
helper/worker double-send path without changing pipeline generation.
"""

import telegram_pipeline_worker as _worker

_original_send_message = _worker.send_message
_original_send_video = _worker.send_video
_state = {"video_caption": "", "video_sent": False, "social_sent": False}


def _send_video(path, caption):
    _state["video_caption"] = str(caption or "")
    _state["video_sent"] = True
    return _original_send_video(path, caption)


def _send_message(text):
    text = str(text or "")
    # Worker sends the title options immediately after the video; preserve it.
    # The next plain social message is the single Threads delivery. Replace that
    # one delivery with a combined Instagram + Threads bundle so the caption is
    # always explicitly visible and Threads can never be sent twice.
    if _state["video_sent"] and not _state["social_sent"] and text and not text.startswith(("🎯", "⏳", "📊")):
        _state["social_sent"] = True
        bundle = (
            "📝 INSTAGRAM AÇIKLAMASI + HASHTAGLER\n\n"
            + _state["video_caption"]
            + "\n\n🧵 THREADS AÇIKLAMASI\n\n"
            + text
        )
        return _original_send_message(bundle)
    return _original_send_message(text)


_worker.send_video = _send_video
_worker.send_message = _send_message

# telegram_pipeline_guard imports the already-loaded worker module and runs it.
import telegram_pipeline_guard  # noqa: E402,F401
