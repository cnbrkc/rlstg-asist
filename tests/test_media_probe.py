"""core/media.py ffprobe yedeği (ffmpeg -i stderr ayrıştırıcı) için testler.

Bu testler gerçek ffmpeg çağırmaz; `_parse_ffmpeg_stderr` saf bir fonksiyon
olduğu için gerçekçi stderr örnekleriyle doğrulanır.
"""
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.media import _parse_ffmpeg_stderr, _ffprobe_yolu

# Tipik bir telefon videosu için ffmpeg -i çıktısı (video + audio).
_SAMPLE_WITH_AUDIO = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'data/IMG_8043.MP4':
  Metadata:
    major_brand     : mp42
  Duration: 00:01:31.83, start: 0.000000, bitrate: 4908 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p, 1080x1920 [SAR 1:1 DAR 9:16], 4908 kb/s, 30 fps, 30 tbr, 90k tbn (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 127 kb/s (default)"""

_SAMPLE_NO_AUDIO = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'silent.mp4':
  Duration: 00:00:10.50, start: 0.000000, bitrate: 2000 kb/s
  Stream #0:0[0x1](und): Video: hevc (Main), yuv420p, 720x1280, 2000 kb/s, 25 fps, 25 tbr"""


class FfmpegStderrParseTests(unittest.TestCase):
    def test_extracts_full_video_and_audio_info(self):
        r = _parse_ffmpeg_stderr(_SAMPLE_WITH_AUDIO)
        self.assertAlmostEqual(r["duration"], 91.83, places=2)
        self.assertEqual((r["width"], r["height"]), (1080, 1920))
        self.assertAlmostEqual(r["fps"], 30.0, places=2)
        self.assertEqual(r["video_codec"], "h264")
        self.assertEqual(r["video_bitrate"], 4908_000)
        self.assertEqual(r["audio_codec"], "aac")
        self.assertEqual(r["audio_sample_rate"], 48000)
        self.assertEqual(r["audio_channels"], 2)
        self.assertEqual(r["audio_bitrate"], 127_000)

    def test_handles_missing_audio_stream(self):
        r = _parse_ffmpeg_stderr(_SAMPLE_NO_AUDIO)
        self.assertAlmostEqual(r["duration"], 10.50, places=2)
        self.assertEqual((r["width"], r["height"]), (720, 1280))
        self.assertAlmostEqual(r["fps"], 25.0, places=1)
        self.assertEqual(r["video_codec"], "hevc")
        self.assertEqual(r["audio_sample_rate"], 0)
        self.assertEqual(r["audio_channels"], 0)
        self.assertEqual(r["audio_codec"], "")

    def test_empty_stderr_yields_zeros(self):
        r = _parse_ffmpeg_stderr("")
        self.assertEqual(r["duration"], 0.0)
        self.assertEqual((r["width"], r["height"]), (0, 0))
        self.assertEqual(r["fps"], 0.0)

    def test_mono_audio_channel_count(self):
        sample = (
            "  Duration: 00:00:05.00\n"
            "    Stream #0:1: Audio: aac, 44100 Hz, mono, fltp, 64 kb/s"
        )
        r = _parse_ffmpeg_stderr(sample)
        self.assertEqual(r["audio_sample_rate"], 44100)
        self.assertEqual(r["audio_channels"], 1)

    def test_ffprobe_yolu_returns_string_or_empty(self):
        # Bu ortamda ffprobe yoksa bile patlamamalı (boş string döner).
        self.assertIsInstance(_ffprobe_yolu(), str)


if __name__ == "__main__":
    unittest.main()
