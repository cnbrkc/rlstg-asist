import unittest

from duo_strategy import normalize_duo_strategy
from duo_script import normalize_conversation_map, validate_script_segments


class DuoLayerTests(unittest.TestCase):
    def test_duo_plan_keeps_both_speakers_and_clamps_levels(self):
        state = {
            "anlatim_modu": "DUO",
            "duo_stratejisi": {
                "hook_speaker": "female",
                "ending_speaker": "male",
                "female_agirligi": 1.4,
                "male_agirligi": -0.2,
                "interaction_level": "0.6",
                "humor_level": 2,
                "tension_level": "bad",
            },
            "konusma_haritasi": [
                {"speaker": "female", "amac": "hook", "detay": "Giriş"},
                {"speaker": "male", "amac": "fact", "detay": "Teknik detay"},
            ],
        }
        plan = normalize_duo_strategy(state)
        self.assertEqual(plan["mode"], "DUO")
        self.assertEqual(plan["hook_speaker"], "female")
        self.assertEqual(plan["ending_speaker"], "male")
        self.assertEqual(plan["female_weight"], 1.0)
        self.assertEqual(plan["male_weight"], 0.0)
        self.assertEqual(plan["interaction_level"], 0.6)
        self.assertEqual(len(plan["conversation_map"]), 2)

    def test_solo_mode_filters_other_speaker(self):
        state = {
            "anlatim_modu": "SOLO_FEMALE",
            "duo_stratejisi": {"hook_speaker": "male", "ending_speaker": "male"},
            "konusma_haritasi": [
                {"speaker": "female", "amac": "hook", "detay": "Kadın"},
                {"speaker": "male", "amac": "fact", "detay": "Erkek"},
            ],
        }
        plan = normalize_duo_strategy(state)
        self.assertEqual(plan["hook_speaker"], "female")
        self.assertEqual(plan["ending_speaker"], "female")
        self.assertEqual([x["speaker"] for x in plan["conversation_map"]], ["female"])

    def test_script_layer_accepts_only_valid_segments(self):
        segments = validate_script_segments([
            {"speaker": "female", "text": "İlk cümle."},
            {"speaker": "robot", "text": "Silinmeli."},
            {"speaker": "male", "text": "İkinci cümle."},
            {"speaker": "female", "text": ""},
        ], "DUO")
        self.assertEqual(segments, [
            {"speaker": "female", "text": "İlk cümle."},
            {"speaker": "male", "text": "İkinci cümle."},
        ])

    def test_conversation_map_uses_normalized_mode(self):
        strategy = {
            "mode": "SOLO_MALE",
            "conversation_map": [
                {"speaker": "female", "amac": "hook", "detay": "Yanlış"},
                {"speaker": "male", "amac": "fact", "detay": "Doğru"},
            ],
        }
        self.assertEqual(normalize_conversation_map(strategy), [
            {"sira": 1, "speaker": "male", "amac": "fact", "detay": "Doğru", "duygu": "natural"}
        ])


if __name__ == "__main__":
    unittest.main()
