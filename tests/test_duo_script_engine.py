import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from duo.duo_script_engine import (
    build_duo_generation_contract,
    build_generation_prompt,
    duo_conversation_quality_issues,
    validate_generated_duo,
)


class DuoScriptEngineTests(unittest.TestCase):
    def test_duo_contract_uses_autonoe_and_charon(self):
        contract = build_duo_generation_contract({
            "mode": "DUO",
            "hook_speaker": "female",
            "ending_speaker": "male",
            "conversation_map": [
                {"speaker": "female", "amac": "hook", "detay": "Giriş"},
                {"speaker": "male", "amac": "counterpoint", "detay": "Karşılık"},
            ],
        })
        self.assertEqual([x["voice"] for x in contract["speakers"]], ["Autonoe", "Charon"])
        self.assertEqual(contract["hook_speaker"], "female")
        self.assertEqual(contract["ending_speaker"], "male")

    def test_solo_contract_removes_other_voice(self):
        contract = build_duo_generation_contract({
            "mode": "SOLO_FEMALE",
            "hook_speaker": "male",
            "ending_speaker": "male",
            "conversation_map": [
                {"speaker": "female", "amac": "hook", "detay": "Kadın"},
                {"speaker": "male", "amac": "fact", "detay": "Erkek"},
            ],
        })
        self.assertEqual([x["voice"] for x in contract["speakers"]], ["Autonoe"])
        self.assertEqual(contract["hook_speaker"], "female")
        self.assertEqual(contract["ending_speaker"], "female")
        self.assertTrue(all(x["speaker"] == "female" for x in contract["conversation_map"]))

    def test_prompt_requires_json_and_fact_lock(self):
        contract = build_duo_generation_contract({"mode": "DUO"})
        prompt = build_generation_prompt(contract, "editorial", "fact lock")
        self.assertIn("Sadece bu JSON'u döndür", prompt)
        self.assertIn("FACT LOCK", prompt)
        self.assertIn("LEXICAL UPTAKE", prompt)
        self.assertIn("HOOK → FRICTION → PROOF → REVERSAL → PAYOFF", prompt)
        self.assertIn("şarkıcı düeti", prompt)
        self.assertIn("reply_anchor", prompt)

    def test_quality_check_accepts_asymmetric_reactive_conversation(self):
        contract = build_duo_generation_contract({"mode": "DUO", "target_words": 70})
        generated = {
            "conversation_design": {
                "central_tension": "global fiyat ile Türkiye gerçeği",
                "hook_open_loop": "bu fiyat neden şaşırtıcı",
                "reversal": "ucuzluğun Türkiye fiyatı olmadığı kabulü",
                "payoff_callback": "fiyat avantajına geri dönüş",
            },
            "segments": [
                {"speaker": "female", "reply_anchor": "OPENING", "text": "Bu fiyat doğruysa kapıyı falan boş ver."},
                {"speaker": "male", "reply_anchor": "bu fiyat", "text": "Doğru, ama Türkiye etiketi değil."},
                {"speaker": "male", "reply_anchor": "Türkiye etiketi", "text": "Kendi pazarında aynı sınıftaki rakiplerinden belirgin biçimde aşağıda başlıyor."},
                {"speaker": "female", "reply_anchor": "rakiplerinden aşağıda", "text": "Tamam, o zaman mesele ucuz görünmesi değil; gerçekten aşağıda olması."},
                {"speaker": "male", "reply_anchor": "gerçekten aşağıda", "text": "Aynen, Türkiye'ye aynı avantajla gelirse kapı detayı sonra konuşulur."},
            ],
        }
        self.assertEqual([], duo_conversation_quality_issues(contract, generated))

    def test_quality_check_flags_token_second_voice_and_duet_cadence(self):
        contract = build_duo_generation_contract({"mode": "DUO", "target_words": 80})
        generated = {
            "segments": [
                {"speaker": "female", "text": "Bir iki üç dört beş altı."},
                {"speaker": "male", "text": "Evet."},
                {"speaker": "female", "text": "Bir iki üç dört beş altı."},
                {"speaker": "female", "text": "Bir iki üç dört beş altı."},
            ]
        }
        issues = duo_conversation_quality_issues(contract, generated)
        self.assertIn("speaker_is_only_token_presence", issues)
        self.assertTrue(any(x.startswith("weak_turn_exchange") for x in issues))
        self.assertIn("insufficient_lexical_uptake", issues)
        self.assertTrue(any(x.startswith("missing_design") for x in issues))

    def test_invalid_generated_speaker_is_rejected(self):
        contract = build_duo_generation_contract({"mode": "DUO"})
        valid = validate_generated_duo(contract, [
            {"speaker": "female", "text": "Tamam."},
            {"speaker": "robot", "text": "Olmaz."},
            {"speaker": "male", "text": "Bakalım."},
        ])
        self.assertEqual(valid, [
            {"speaker": "female", "text": "Tamam."},
            {"speaker": "male", "text": "Bakalım."},
        ])


if __name__ == "__main__":
    unittest.main()
