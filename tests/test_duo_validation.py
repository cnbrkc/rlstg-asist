"""duo_script_engine.validate_generated_duo için esnek kelime sayısı testleri.

Eski sürüm hedefe ±%10 uymayan her script'i reddedip split_for_speakers
yedeğine (daha kısa/düşük kalite) düşürüyordu. Yeni sürüm yalnızca belirgin
şekilde kırık çıktıları reddeder; süre/oran denetimi FFmpeg senkron katmanında.
"""
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duo.duo_script_engine import build_duo_generation_contract, validate_generated_duo


def _contract(min_words, max_words):
    return build_duo_generation_contract({
        "mode": "DUO",
        "min_words": min_words,
        "max_words": max_words,
        "conversation_map": [
            {"speaker": "female", "amac": "hook", "detay": "Açılış"},
            {"speaker": "male", "amac": "fact", "detay": "Detay"},
        ],
    })


class LenientDuoValidationTests(unittest.TestCase):
    def test_accepts_moderately_short_two_speaker_script(self):
        # Hedef 200 kelime; model 92 kelime üretti (eski kuralla reddedilirdi).
        contract = _contract(200, 240)
        female_text = " ".join(["kelime"] * 90)
        generated = {
            "segments": [
                {"speaker": "female", "text": female_text},
                {"speaker": "male", "text": "tamam anladım"},
            ]
        }
        self.assertEqual(validate_generated_duo(contract, generated), generated["segments"])

    def test_rejects_severely_short_script(self):
        # Hedef 200 kelime; 4 kelime = hedefin %2'si -> kırık, reddedilir.
        contract = _contract(200, 240)
        generated = {
            "segments": [
                {"speaker": "female", "text": "a b"},
                {"speaker": "male", "text": "c d"},
            ]
        }
        self.assertEqual(validate_generated_duo(contract, generated), [])

    def test_rejects_single_speaker_in_duo(self):
        contract = _contract(200, 240)
        female_text = " ".join(["kelime"] * 200)
        generated = {
            "segments": [
                {"speaker": "female", "text": female_text},
                {"speaker": "female", "text": female_text},
            ]
        }
        self.assertEqual(validate_generated_duo(contract, generated), [])

    def test_rejects_hugely_inflated_script(self):
        contract = _contract(10, 12)
        big = " ".join(["kelime"] * 500)  # max_words*3 = 36'in çok üstünde
        generated = {
            "segments": [
                {"speaker": "female", "text": big},
                {"speaker": "male", "text": "ekle"},
            ]
        }
        self.assertEqual(validate_generated_duo(contract, generated), [])


if __name__ == "__main__":
    unittest.main()
