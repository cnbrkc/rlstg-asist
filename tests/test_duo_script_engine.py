import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from duo.duo_script_engine import build_duo_generation_contract, build_generation_prompt, validate_generated_duo


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
        self.assertIn("GERÇEK KARŞILIKLILIK", prompt)
        self.assertIn("ÖLÇÜLÜ ÇEKİŞME", prompt)

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
