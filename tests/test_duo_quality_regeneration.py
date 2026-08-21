import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.pipeline import _duo_script_calistir


class _Router:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def metin_uret(self, *args, **kwargs):
        response = self.responses[self.calls]
        self.calls += 1
        return response, f"fake-model-{self.calls}"


def _design():
    return {
        "central_tension": "fiyat avantajı ile Türkiye fiyatı farkı",
        "hook_open_loop": "fiyat neden şaşırtıcı",
        "reversal": "Türkiye etiketi olmadığı kabulü",
        "payoff_callback": "fiyat avantajına dönüş",
    }


class DuoQualityRegenerationTests(unittest.TestCase):
    def test_one_targeted_rewrite_replaces_duet_like_valid_script(self):
        poor = {
            "conversation_design": _design(),
            "segments": [
                {"speaker": "female", "purpose": "hook", "reply_anchor": "OPENING", "text": "Bu araç gerçekten çok uygun bir fiyatla dikkat çekiyor ve herkesin ilgisini kolayca çekebilir."},
                {"speaker": "male", "purpose": "fact", "reply_anchor": "", "text": "Evet."},
                {"speaker": "female", "purpose": "fact", "reply_anchor": "", "text": "Üstelik tasarımı da modern görünüyor ve günlük kullanıma uygun birçok özellik sunuyor."},
                {"speaker": "female", "purpose": "closing", "reply_anchor": "", "text": "Sonuç olarak bu otomobil fiyatıyla ve tasarımıyla pazarda öne çıkabilecek bir seçenek."},
            ],
        }
        good = {
            "conversation_design": _design(),
            "segments": [
                {"speaker": "female", "purpose": "hook", "reply_anchor": "OPENING", "text": "Kapıyı bırak, şu fiyata bak."},
                {"speaker": "male", "purpose": "rebuttal", "reply_anchor": "şu fiyat", "text": "Bakıyorum da o Türkiye fiyatı değil."},
                {"speaker": "male", "purpose": "fact", "reply_anchor": "Türkiye fiyatı değil", "text": "Kendi pazarında doğrulanmış başlangıç etiketi ve rakiplerinden belirgin biçimde aşağıda."},
                {"speaker": "female", "purpose": "counterpoint", "reply_anchor": "rakiplerinden aşağıda", "text": "Tamam, işte bu kapı kolundan daha büyük mesele."},
                {"speaker": "male", "purpose": "concession", "reply_anchor": "daha büyük mesele", "text": "Orada haklısın; aynı avantajla gelse burada asıl onu konuşurduk."},
            ],
        }
        router = _Router([poor, good])
        logs = []
        result = _duo_script_calistir(
            router,
            {"mode": "DUO", "target_words": 70, "min_words": 55, "max_words": 85},
            {"core_story": "fiyat"},
            {"facts": []},
            {},
            logs.append,
        )
        self.assertEqual(2, router.calls)
        self.assertEqual("ready", result["status"])
        self.assertEqual([], result["conversation_quality_issues"])
        self.assertEqual("Kapıyı bırak, şu fiyata bak.", result["segments"][0]["text"])
        self.assertTrue(any("tek kalite yenilemesi" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
