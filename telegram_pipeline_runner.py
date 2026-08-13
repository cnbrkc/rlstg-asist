import os
import subprocess
from pathlib import Path
import media


def stable_render(video_path, audio_path, output_path, log):
    if not audio_path or not os.path.exists(audio_path):
        return False
    cmd = [media.FFMPEG_BIN, "-y", "-i", str(video_path), "-i", str(audio_path), "-filter_complex", "[1:a]apad[a]", "-map", "0:v:0", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", str(getattr(media, "VIDEO_CRF", 20)), "-c:a", "aac", "-ar", "48000", "-ac", "1", "-b:a", "192k", "-shortest", str(output_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            log(f"⚠️ Stabil FFmpeg render hatası: {(r.stderr or '')[-800:]}")
            return False
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as exc:
        log(f"⚠️ Stabil FFmpeg render hatası: {exc}")
        return False

media.video_ve_sesi_birlestir = stable_render
import telegram_pipeline_worker as worker
worker._extract_video_ocr = lambda path: ""

if __name__ == "__main__":
    worker.main()
