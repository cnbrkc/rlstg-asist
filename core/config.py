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


# GÜNCEL MODELLER (Eylül 2026) - Ücretli olan Pro modelleri ve limiti düşük olanlar çıkarıldı.
# Sadece ücretsiz (Free Tier) API'de yüksek limitli çalışan Flash serisi bırakıldı.
#
# SIRALAMA (Eylül 2026 üretim logları): gemini-3.5-flash-lite ve gemini-3.6-flash
# fiilen yanıt veren modeller; 3.8/3.7/3.5-flash ve 3.1-flash-lite yoğunlukta
# neredeyse her denemede anında 503 döndü. Kanıtlanmış modeller öne alındı ki
# ilk model düştüğünde ikinci denemede hemen çalışan modele geçilsin.

VIDEO_ANALIZ_MODELLERI = [
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]

METIN_MODELLERI = [
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]

ARAMA_MODELLERI = [
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


# İstek başına zaman aşımı (ms). 60 sn'lik tek değer, yoğunlukta asılı kalan her
# denemeye 1 dakika kaybettiriyordu (bir Critic çağrısı 236 sn sürdü). Kısa JSON
# üreten ajanlar için 30-45 sn fazlasıyla yeterli; yalnızca uzun çıktılı adımlar
# (Editorial / QA / Fact Lock), video analizi ve TTS daha geniş pay alır.
ISTEK_ZAMAN_ASIMI_MS = {
    "metin": _env_int("ROUTER_TEXT_TIMEOUT_MS", 45_000),
    "uzun_metin": _env_int("ROUTER_LONG_TEXT_TIMEOUT_MS", 60_000),
    "istege_bagli": _env_int("ROUTER_OPTIONAL_TIMEOUT_MS", 30_000),
    "video": _env_int("ROUTER_VIDEO_TIMEOUT_MS", 120_000),
    "tts": _env_int("ROUTER_TTS_TIMEOUT_MS", 60_000),
}
# Güvenli varsayılanı olan isteğe bağlı ajanlar (Detective / Hook / Critic /
# Metadata / anlatım modu) için istek başına toplam süre bütçesi (sn).
# 60 sn'lik bütçe tek bir aşırı-yük bekleme turuna bile yetmiyordu; isteğe bağlı
# ajanlar yoğunlukta pratikte hiç ikinci şans alamıyor ve kalite (Detective /
# Critic / Hook çıktısı) varsayılana düşüyordu. 120 sn: tam tur + 15/30 sn
# bekleme + tekrar tur sığar; asılı kalan istekler için güvenlik sınırı korunur.
ISTEGE_BAGLI_AJAN_BUTCESI_SANIYE = _env_int("ROUTER_OPTIONAL_BUDGET_SECONDS", 120)
# Router bu kadar saniye içinde "tam tur aşırı yük" gördüyse atlanabilir ajanlar
# (Detective / Critic / anlatım modu) hiç denenmeden güvenli varsayılanla geçilir.
# KALİTE: Bu atlama artık varsayılan olarak KAPALI (ISTEGE_BAGLI_ASIRI_YUK_ATLA=1
# ile açılır); ajanlar her zaman denenir.
ASIRI_YUK_ATLAMA_PENCERESI_SANIYE = _env_int("ROUTER_OVERLOAD_SKIP_WINDOW", 90)

SES_MODELLERI = [
    # gemini-3.1-flash-tts-preview bu kodun gönderdiği multi-speaker/prebuilt
    # voice config'iyle sürekli 400 (model_config) hatası veriyor; bu yüzden
    # çalışan gemini-2.5-flash-preview-tts önceliklendirildi.
    "gemini-2.5-flash-preview-tts",
    "gemini-3.1-flash-tts-preview",
]


COOLDOWN_BULUNAMADI = 24 * 60 * 60
COOLDOWN_DIGER = 5 * 60
COOLDOWN_FREE_TIER_YOK = 7 * 24 * 60 * 60

KELIME_HIZI_ORANI = 2.9
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
    "🎙️ Agentic üretim döngüsü (Detective + Hook + Script + Critic)...",
    "📝 Caption + hashtag hazırlanıyor...",
    "🧵 Threads hazırlanıyor...",
    "🔍 Son kalite kontrol (QA)...",
    "🎧 Ses üretiliyor (TTS)...",
    "🎬 Video hazırlanıyor (render)...",
]

def model_arama_destekliyor_mu(model_adi: str) -> bool:
    return model_adi in ARAMA_MODELLERI


def _env_bool(ad, varsayilan=False):
    deger = os.getenv(ad)
    if deger is None or not str(deger).strip():
        return varsayilan
    return str(deger).strip().lower() in {"1", "true", "yes", "evet", "on"}


# Hız-öncelikli operasyon anahtarları (varsayılan KAPALI = kalite öncelikli).
#   ISTEGE_BAGLI_HIZLI_BASARISIZLIK=1 : isteğe bağlı ajanlar aşırı yükte beklemeden
#                                       tek tam turla vazgeçer.
#   ISTEGE_BAGLI_ASIRI_YUK_ATLA=1     : yakın zamanda tam-tur aşırı yük varsa
#                                       isteğe bağlı ajanlar hiç denenmez.
ISTEGE_BAGLI_HIZLI_BASARISIZLIK = _env_bool("ISTEGE_BAGLI_HIZLI_BASARISIZLIK", False)
ISTEGE_BAGLI_ASIRI_YUK_ATLA = _env_bool("ISTEGE_BAGLI_ASIRI_YUK_ATLA", False)
