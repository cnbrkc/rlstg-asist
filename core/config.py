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

# GÜNCEL MODELLER (Ağustos 2026) - 404 veren eski lite sürümleri kaldırıldı.
# 3.x serisi güncel, stabil ve hızlı olduğu için önceliklendirildi.
VIDEO_ANALIZ_MODELLERI = [
    "gemini-3.7-flash", 
    "gemini-3.6-flash", 
    "gemini-3.5-flash", 
    "gemini-3.1-pro-preview", 
    "gemini-2.5-pro",
    "gemini-2.5-flash"
]

METIN_MODELLERI = [
    "gemini-3.7-flash", 
    "gemini-3.6-flash", 
    "gemini-3.5-flash", 
    "gemini-3.1-pro-preview", 
    "gemini-3.5-flash-lite", 
    "gemini-3.1-flash-lite", 
    "gemini-2.5-flash"
]

ARAMA_MODELLERI = [
    "gemini-3.7-flash", 
    "gemini-3.6-flash", 
    "gemini-3.5-flash", 
    "gemini-3.1-pro-preview",
    "gemini-2.5-flash"
]

SES_MODELLERI = [
    # gemini-3.1-flash-tts-preview bu kodun gönderdiği multi-speaker/prebuilt
    # voice config'iyle sürekli 400 (model_config) hatası veriyor; bu yüzden
    # çalışan gemini-2.5-flash-preview-tts önceliklendirildi. (Kalıcı ban
    # mekanizması zaten 3.1-flash-tts-preview'i ilk hatadan sonra atlar, ancak
    # her çalışmanın ilk TTS çağrısındaki boş denemeyi de böyle önlemiş oluyoruz.)
    "gemini-2.5-flash-preview-tts",
    "gemini-3.1-flash-tts-preview",
]

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
    return model_adi in ARAMA_MODELLERI
