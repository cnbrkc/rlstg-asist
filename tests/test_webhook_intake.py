from telegram.telegram_webhook_intake import _safe_filename


def test_safe_filename_removes_paths_controls_and_commas():
    assert _safe_filename("../../my video,final.MP4") == "my_video_final.MP4"
    assert _safe_filename(r"..\folder\clip.mov") == "clip.mov"


def test_safe_filename_has_fallback_and_limit():
    assert _safe_filename("../..") == "telegram_video.mp4"
    assert len(_safe_filename("x" * 300 + ".mp4")) <= 160
