import os

os.environ.setdefault("GEMINI_API_KEY", "test")

from core.prompts import reels_creative_promptunu_olustur, research_promptunu_olustur


def test_reels_prompt_uses_user_override_then_ai_editorial_mode_choice():
    prompt = reels_creative_promptunu_olustur(30, "dengeli")
    assert "Kullanıcı ses modu belirtmediyse" in prompt
    assert "en güçlü modu SEN seç" in prompt
    assert "HER ZAMAN DUO" not in prompt
    assert "düet" in prompt
    assert "ölçülü bir kutuplaşma" in prompt


def test_active_research_prompt_has_no_unresolved_legacy_placeholder():
    prompt = research_promptunu_olustur()
    assert "{guncellik_talimati}" not in prompt
    assert "kurallar.txt" not in prompt
