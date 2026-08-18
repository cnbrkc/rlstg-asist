# GitHub Actions / Telegram sürümü için yapılandırma.
import os

API_KEYS = {}
_single_key = os.environ.get("GEMINI_API_KEY", "").strip()
if _single_key:
    API_KEYS["GEMINI_API_KEY"] = _single_key
for _i, _key in enumerate(os.environ.get("GEMINI_API_KEYS", "").split(","), 1):
    _key = _key.strip()
    if _key:
        API_KEYS.setdefault(f"GEMINI_API_KEY_{_i}", _key)
for _i in range(1, 21):
    _key = os.environ.get(f"GEMINI_API_KEY_{_i}", "").strip()
    if _key:
        API_KEYS.setdefault(f"GEMINI_API_KEY_{_i}", _key)
if not API_KEYS:
    raise RuntimeError("GEMINI_API_KEY secret bulunamadı.")

# 2.5-flash-lite yeni hesaplarda sıkça 404 verdiği için listeden çıkarıldı.
VIDEO_ANALIZ_MODELLERI = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
METIN_MODELLERI = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
ARAMA_MODELLERI = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
SES_MODELLERI = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]

COOLDOWN_SUNUCU = 30
COOLDOWN_BULUNAMADI = 24 * 60 * 60
COOLDOWN_DIGER = 5 * 60
COOLDOWN_FREE_TIER_YOK = 7 * 24 * 60 * 60

KELIME_HIZI_ORANI = 2.4
TON_EGLENCE = "eglence"
TON_DENGELI = "dengeli"
TON_BILGI = "bilgi"
TON_TEKNIK = "teknik"
SES_ORNEK_HIZI = 24000
SES_KANAL = 1
SES_GENISLIK = 2
VIDEO_CRF = 20
VIDEO_PRESET = "veryfast"
SES_HIZ_CARPANI = 1.2
PIPELINE_ADIMLARI = [
    "🎥 Video analiz ediliyor (Forensic)...",
    "🔎 Gerçekler doğrulanıyor (Research / Fact Lock)...",
    "🧠 Hikâye seçiliyor (Editorial Brain)...",
    "🎙️ Reels hazırlanıyor (Cover + Hook + Voiceover)...",
    "📝 Caption + hashtag hazırlanıyor...",
    "🧵 Threads hazırlanıyor...",
    "🔍 Son kalite kontrol (QA)...",
    "🎧 Ses üretiliyor (TTS)...",
    "🎬 Video hazırlanıyor (render)...",
]


def model_arama_destekliyor_mu(model_adi: str) -> bool:
    return model_adi.startswith("gemini-2.5") or model_adi.startswith("gemini-3")
