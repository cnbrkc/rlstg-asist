from telegram.telegram_webhook_intake import _safe_filename


def test_safe_filename_removes_paths_controls_and_commas():
    assert _safe_filename("../../my video,final.MP4") == "my_video_final.MP4"
    assert _safe_filename(r"..\folder\clip.mov") == "clip.mov"


def test_safe_filename_has_fallback_and_limit():
    assert _safe_filename("../..") == "telegram_video.mp4"
    assert len(_safe_filename("x" * 300 + ".mp4")) <= 160

def test_video_indir_writes_file_and_uses_timeout(tmp_path, monkeypatch):
    import io

    import telegram.telegram_webhook_intake as intake

    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return io.BytesIO(b"videobayt")

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        captured["url"] = getattr(req, "full_url", "")
        return _FakeResponse()

    monkeypatch.setattr(intake.urllib.request, "urlopen", fake_urlopen)
    hedef = tmp_path / "video.mp4"
    intake._videoyi_indir("https://api.telegram.org/file/botXXX/x", hedef)
    assert hedef.read_bytes() == b"videobayt"
    assert captured["timeout"] == 60

