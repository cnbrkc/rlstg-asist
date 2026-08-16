import telegram_pipeline_worker as worker

# Telegram pipeline uses the canonical media.video_ve_sesi_birlestir implementation.
# Do not replace it here: the media layer owns FPS preservation, controlled upscale,
# 1.20x audio muxing and final FFmpeg validation.
worker._extract_video_ocr = lambda path: ""

if __name__ == "__main__":
    worker.main()
