"""Gemini API model IDs follow the current stable catalog without stale aliases."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core import config


def test_flash_fallbacks_prioritize_current_stable_models():
    for models in (config.VIDEO_ANALIZ_MODELLERI, config.METIN_MODELLERI, config.ARAMA_MODELLERI):
        without_optional_argon = [model for model in models if model != "gemini-4-argon"]
        assert without_optional_argon[:4] == [
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.1-flash-lite",
        ]
        # These are deprecated aliases that already auto-route to the stable ID.
        assert "gemini-3.7-flash" not in models
        assert "gemini-3.5-flash" not in models


def test_paid_only_argon_is_explicitly_opt_in():
    enabled = "gemini-4-argon" in config.METIN_MODELLERI
    assert enabled is config.GEMINI_ENABLE_GEMINI_4_ARGON
    assert enabled is ("gemini-4-argon" in config.VIDEO_ANALIZ_MODELLERI)
    assert enabled is ("gemini-4-argon" in config.ARAMA_MODELLERI)


def test_tts_prefers_current_stable_generation_and_keeps_bounded_legacy_fallback():
    assert config.SES_MODELLERI == [
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-lite-tts",
    ]
    assert config.SES_LEGACY_MODELLERI == ["gemini-2.5-flash-preview-tts"]
