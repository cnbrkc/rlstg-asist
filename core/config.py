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


def _env_bool(ad, varsayilan=False):
    deger = os.getenv(ad)
    if deger is None or not str(deger).strip():
        return varsayilan
    return str(deger).strip().lower() in {"1", "true", "yes", "evet", "on"}


# Gemini API model rotasyonu (18.10.2026).
# Gemini 3.8 Flash kararlı sürüm olarak en yetenekli ve dengeli varsayılandır.
# Gemini 3.5 Flash-Lite / 3.6 Flash, mevcut üretim loglarında çalışan hızlı
# yedeklerdir. 3.7 Flash -> 3.8 Flash ve 3.5 Flash -> 3.6 Flash artık alias
# olarak otomatik yönlendirildiğinden gereksiz denemeleri kaldırdık.
#
# Gemini 4 Argon kararlı ve en güçlü reasoning modelidir; ancak Google'ın resmi
# fiyatlandırmasına göre Free Tier'da YOKTUR ve çıktı tokenı maliyeti yüksektir.
# Ücretli proje kullananlar açıkça GEMINI_ENABLE_GEMINI_4_ARGON=1 diyerek açar.
# Varsayılan kapalı: ücretsiz anahtarlarda kota-0/429 ve beklenmeyen ücret olmaz.
GEMINI_ENABLE_GEMINI_4_ARGON = _env_bool("GEMINI_ENABLE_GEMINI_4_ARGON", False)
GEMINI_PREMIUM_MODELLERI = ["gemini-4-argon"] if GEMINI_ENABLE_GEMINI_4_ARGON else []

# Üretim loglarına göre (19.10.2026): gemini-3.8-flash 3 anahtar ile
# ~407 saniye boşa gitti (kota/503/timeout), gemini-3.5-flash-lite 19 sn'de
# başarılı oldu. Çalışan ve hızlı model önceliklendirilir; 3.8-flash yedek
# olarak kalır. Sıralama buradan akıştaki tüm model listelerine (video, metin,
# arama) döker.
_GEMINI_FLASH_FALLBACKS = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.1-flash-lite",
]

VIDEO_ANALIZ_MODELLERI = [*GEMINI_PREMIUM_MODELLERI, *_GEMINI_FLASH_FALLBACKS]
METIN_MODELLERI = [*GEMINI_PREMIUM_MODELLERI, *_GEMINI_FLASH_FALLBACKS]
ARAMA_MODELLERI = [*GEMINI_PREMIUM_MODELLERI, *_GEMINI_FLASH_FALLBACKS]


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
# Her zorunlu Router isteği için üst süre sınırı. Timeout model×key denemesi
# başına uygulandığından bu toplam bütçeler tek isteğin bütün fallback/retry
# turlarını sınırlar; 30 dakikalık GitHub Actions job'unu tek API çağrısına
# kaptırmaz. Operasyonel olarak ROUTER_*_BUDGET_SECONDS ile ayarlanabilir;
# 0 verilirse ilgili profile toplam süre sınırı uygulanmaz.
ISTEK_BUTCELERI_SANIYE = {
    "metin": _env_int("ROUTER_TEXT_BUDGET_SECONDS", 180),
    "uzun_metin": _env_int("ROUTER_LONG_TEXT_BUDGET_SECONDS", 240),
    "istege_bagli": ISTEGE_BAGLI_AJAN_BUTCESI_SANIYE,
    "video": _env_int("ROUTER_VIDEO_BUDGET_SECONDS", 420),
    "tts": _env_int("ROUTER_TTS_BUDGET_SECONDS", 240),
}
# Router bu kadar saniye içinde "tam tur aşırı yük" gördüyse atlanabilir ajanlar
# (Detective / Critic / anlatım modu) hiç denenmeden güvenli varsayılanla geçilir.
# KALİTE: Bu atlama artık varsayılan olarak KAPALI (ISTEGE_BAGLI_ASIRI_YUK_ATLA=1
# ile açılır); ajanlar her zaman denenir.
ASIRI_YUK_ATLAMA_PENCERESI_SANIYE = _env_int("ROUTER_OVERLOAD_SKIP_WINDOW", 90)

# 3.8 TTS kararlı modeldir; GenerateContent'te speech_metadata ve WAV içerik
# çıktısı kullanılır. Router 3.8 isteği başarısız olursa, 17 Kasım 2026
# kapanışına kadar geçici uyumluluk için legacy preview'a düşer.
SES_MODELLERI = [
    "gemini-3.8-flash-tts",
    "gemini-3.8-flash-lite-tts",
]
SES_LEGACY_MODELLERI = ["gemini-2.5-flash-preview-tts"]


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
# TTS atempo çarpanı. TEK DOĞRULUK KAYNAĞI burasıdır: hem core.media/core.router
# sesi bu değerle hızlandırır hem de Telegram raporu aynı sabiti okur (eski
# sürümde rapor satırına değer sabit metin olarak yazılmıştı; çarpan
# değiştiğinde rapor gerçeği yansıtmıyordu). Bu değeri değiştirdiğinde başka
# hiçbir dosyayı güncellemen gerekmez. Kullanıcı kararı (18.10.2026): 1.3.
SES_HIZ_CARPANI = 1.3
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

# Telegram Bot API dosya limiti (varsayılan 45 MB; resmi limit 50 MB, başlık/multipart payı için 5 MB tampon).
# Büyük giriş videosu → büyük çıkış videosu → HTTP 413 (Request Entity Too Large) hatasının kök nedeni budur.
# Çıkış videosu bu eşiği aşarsa pipeline otomatik sıkıştırma denemeleri yapar (CRF → 720p → 30fps kademeli).
TELEGRAM_VIDEO_MAX_MB = _env_int("TELEGRAM_VIDEO_MAX_MB", 45)
TELEGRAM_VIDEO_MAX_BYTES = TELEGRAM_VIDEO_MAX_MB * 1024 * 1024
# Telegram'a gönderilecek videoda uzun kenarın tavanı (portrait 1080x1920 → 720x1280). 1280 = 720p'nin uzun kenarı.
TELEGRAM_VIDEO_MAX_DIMENSION = _env_int("TELEGRAM_VIDEO_MAX_DIMENSION", 1280)
# Sıkıştırma kademelerinde kullanılacak CRF değerleri (küçük = kaliteli/büyük, büyük = düşük kalite/küçük).
TELEGRAM_VIDEO_COMPRESS_CRF_LADDER = [26, 28, 30]
# Kaynak videonun analiz için Telegram'dan indirilmesinde uyarı eşiği (MB).
TELEGRAM_INPUT_WARN_MB = _env_int("TELEGRAM_INPUT_WARN_MB", 20)

def model_arama_destekliyor_mu(model_adi: str) -> bool:
    return model_adi in ARAMA_MODELLERI


# Hız-öncelikli operasyon anahtarları (varsayılan KAPALI = kalite öncelikli).
#   ISTEGE_BAGLI_HIZLI_BASARISIZLIK=1 : isteğe bağlı ajanlar aşırı yükte beklemeden
#                                       tek tam turla vazgeçer.
#   ISTEGE_BAGLI_ASIRI_YUK_ATLA=1     : yakın zamanda tam-tur aşırı yük varsa
#                                       isteğe bağlı ajanlar hiç denenmez.
ISTEGE_BAGLI_HIZLI_BASARISIZLIK = _env_bool("ISTEGE_BAGLI_HIZLI_BASARISIZLIK", False)
ISTEGE_BAGLI_ASIRI_YUK_ATLA = _env_bool("ISTEGE_BAGLI_ASIRI_YUK_ATLA", False)
