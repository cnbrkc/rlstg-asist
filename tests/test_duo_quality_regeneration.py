import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.pipeline import agentic_icerik_uretimi


class _Router:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def metin_uret(self, *args, **kwargs):
        response = self.responses[self.calls % len(self.responses)]
        self.calls += 1
        return response, f"fake-model-{self.calls}"

    def ses_uret(self, text, voice, output, log, hiz_carpani=1.0):
        Path(output).write_bytes(b"fake-audio")
        return True, "fake-tts"


def _detective():
    return {
        "kronik_sikayetler": ["Yakıt tüketimi yüksek"],
        "turkiye_ozel_magduriyet": "ÖTV dilimi dezavantajı",
        "viral_kan_mali": "Türkiye fiyatı Avrupa'nın 2 katı"
    }


def _hook():
    return {
        "secilen_sablon": "Ters_Kose",
        "kapak_metni": "Fiyat Şoku",
        "ilk_3_saniye_kanca": "Bu fiyat herkesi şaşırttı"
    }


def _script(segments, question="Siz ne düşünüyorsunuz?"):
    return {
        "segments": segments,
        "yorum_tetikleyici_soru": question
    }


def _critic(score, approved, feedback=""):
    return {
        "score": score,
        "approved": approved,
        "feedback": feedback
    }


def _metadata():
    return {
        "reels_baslik": "Fiyat Şoku",
        "reels_aciklama": "Bu fiyat herkesi şaşırttı",
        "reels_hashtag": ["#otomobil", "#fiyat"]
    }


class AgenticQualityRegenerationTests(unittest.TestCase):
    
    def test_critic_reject_triggers_single_rewrite(self):
        """Critic red verirse Script Writer 1 kez yeniden yazmalı."""
        poor_script = _script([
            {"speaker": "female", "tts_tag": "[vurgulu]", "text": "Bu araç çok iyi."},
            {"speaker": "male", "tts_tag": "", "text": "Evet."}
        ])
        good_script = _script([
            {"speaker": "female", "tts_tag": "[şaşırarak]", "text": "Kapıyı bırak, şu fiyata bak."},
            {"speaker": "male", "tts_tag": "[gülerek]", "text": "Bakıyorum da o Türkiye fiyatı değil."}
        ])
        
        router = _Router([
            _detective(), _hook(), poor_script, 
            _critic(4, False, "Daha doğal yap."), 
            good_script, 
            _metadata()
        ])
        logs = []
        
        reels, model, plan, script, ses_ok, ses_model, ses_modu, ses_dosyasi, meta = agentic_icerik_uretimi(
            router, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "DUO"}
        )
        
        self.assertEqual("ready", script["status"])
        self.assertEqual("Kapıyı bırak, şu fiyata bak.", script["segments"][0]["text"])
        self.assertTrue(any("Revize başlatılıyor" in line for line in logs))
    
    def test_critic_approve_no_rewrite(self):
        """Critic onay verirse yeniden yazım olmamalı."""
        good_script = _script([
            {"speaker": "female", "tts_tag": "[şaşırarak]", "text": "Kapıyı bırak, şu fiyata bak."},
            {"speaker": "male", "tts_tag": "[gülerek]", "text": "Bakıyorum da o Türkiye fiyatı değil."}
        ])
        
        router = _Router([
            _detective(), _hook(), good_script, 
            _critic(8, True), 
            _metadata()
        ])
        logs = []
        
        reels, model, plan, script, ses_ok, ses_model, ses_modu, ses_dosyasi, meta = agentic_icerik_uretimi(
            router, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "DUO"}
        )
        
        self.assertEqual("ready", script["status"])
        self.assertFalse(any("Revize başlatılıyor" in line for line in logs))
    
    def test_tts_tags_preserved_in_segments(self):
        """TTS etiketleri segmentlerde korunmalı."""
        script_data = _script([
            {"speaker": "female", "tts_tag": "[şaşırarak]", "text": "Bu fiyat gerçek mi?"},
            {"speaker": "male", "tts_tag": "[gülerek]", "text": "Maalesef gerçek."}
        ])
        
        router = _Router([
            _detective(), _hook(), script_data, 
            _critic(8, True), 
            _metadata()
        ])
        logs = []
        
        reels, model, plan, script, ses_ok, ses_model, ses_modu, ses_dosyasi, meta = agentic_icerik_uretimi(
            router, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "DUO"}
        )
        
        self.assertEqual("ready", script["status"])
        self.assertTrue(any("[şaşırarak]" in seg.get("text", "") for seg in script["segments"]))
    
    def test_comment_trigger_added_as_final_segment(self):
        """Yorum tetikleyici soru final segment olarak eklenmeli."""
        script_data = _script([
            {"speaker": "female", "tts_tag": "", "text": "Bu araç çok iyi."}
        ], question="Bu fiyat normal mi sizce?")
        
        router = _Router([
            _detective(), _hook(), script_data, 
            _critic(8, True), 
            _metadata()
        ])
        logs = []
        
        reels, model, plan, script, ses_ok, ses_model, ses_modu, ses_dosyasi, meta = agentic_icerik_uretimi(
            router, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "DUO"}
        )
        
        self.assertEqual("ready", script["status"])
        last_segment = script["segments"][-1]
        self.assertIn("Bu fiyat normal mi sizce?", last_segment.get("text", ""))
        self.assertIn("[vurgulu]", last_segment.get("text", ""))
    
    def test_solo_female_locks_speaker(self):
        """SOLO_FEMALE modunda tüm speaker'lar female olmalı."""
        script_data = _script([
            {"speaker": "female", "tts_tag": "", "text": "Bu araç çok iyi."},
            {"speaker": "male", "tts_tag": "", "text": "Evet."}
        ])
        
        router = _Router([
            _detective(), _hook(), script_data, 
            _critic(8, True), 
            _metadata()
        ])
        logs = []
        
        reels, model, plan, script, ses_ok, ses_model, ses_modu, ses_dosyasi, meta = agentic_icerik_uretimi(
            router, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "SOLO_FEMALE"}
        )
        
        self.assertEqual("ready", script["status"])
        speakers = {seg.get("speaker") for seg in script["segments"]}
        self.assertEqual({"female"}, speakers)
    
    def test_solo_male_locks_speaker(self):
        """SOLO_MALE modunda tüm speaker'lar male olmalı."""
        script_data = _script([
            {"speaker": "female", "tts_tag": "", "text": "Bu araç çok iyi."},
            {"speaker": "male", "tts_tag": "", "text": "Evet."}
        ])
        
        router = _Router([
            _detective(), _hook(), script_data, 
            _critic(8, True), 
            _metadata()
        ])
        logs = []
        
        reels, model, plan, script, ses_ok, ses_model, ses_modu, ses_dosyasi, meta = agentic_icerik_uretimi(
            router, {}, {}, {}, 30, "dengeli", "Charon", logs.append, mod_karari={"mode": "SOLO_MALE"}
        )
        
        self.assertEqual("ready", script["status"])
        speakers = {seg.get("speaker") for seg in script["segments"]}
        self.assertEqual({"male"}, speakers)


if __name__ == "__main__":
    unittest.main()
