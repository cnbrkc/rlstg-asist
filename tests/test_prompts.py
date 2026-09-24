import os

os.environ.setdefault("GEMINI_API_KEY", "test")

from core.prompts import research_promptunu_olustur


def test_active_research_prompt_has_no_unresolved_legacy_placeholder():
    prompt = research_promptunu_olustur()
    assert "{guncellik_talimati}" not in prompt
    assert "kurallar.txt" not in prompt
