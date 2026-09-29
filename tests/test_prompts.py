import os

os.environ.setdefault("GEMINI_API_KEY", "test")

from core.prompts import research_promptunu_olustur


def test_active_research_prompt_has_no_unresolved_legacy_placeholder():
    prompt = research_promptunu_olustur()
    assert "{guncellik_talimati}" not in prompt
    assert "kurallar.txt" not in prompt

def test_forensic_prompt_user_notes_placeholder_appears_once():
    """Kullanıcı notu prompta BİR KEZ girmeli: {ek_notlar_bolumu} iki kez
    duruyorsa str.replace notu iki kez enjekte ediyor (prompt şişiyor)."""
    from pathlib import Path

    from core.prompts import forensic_analiz_promptunu_olustur

    sablon = Path("core/prompts/forensic_analysis_prompt.txt").read_text(encoding="utf-8")
    assert sablon.count("{ek_notlar_bolumu}") == 1
    prompt = forensic_analiz_promptunu_olustur(ek_notlar="KULLANICI NOTU-İŞARETİ", sure_saniye=30)
    assert prompt.count("KULLANICI NOTU-İŞARETİ") == 1

