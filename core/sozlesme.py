"""Üretim sözleşmesinin sayısal eşikleri için TEK doğruluk kaynağı.

Bu değerler prompt dosyalarında, şemalarda ve kodda tekrar eder; kaynaklar
arası kayma (#7/#8 tarzı) üretici-hakem çelişkisi yaratır (ör. üretici promptu
"650-750 olabilir" derken QA promptu "700 altı FAIL" diyordu → 680 karakterlik
yasal caption zorunlu QA FAIL'e düşüyordu).

tests/test_sozlesme_tutarliligi.py bu sabitleri prompt ve kod tarafıyla
karşılaştırır: bir tarafı değiştirirsen test kırılır; senkronu elle tutmak
gerekmez. Değer değişikliği BURADAN yapılır, prompt/kod tarafı bundan türer.
"""

# --- Caption uzunluğu (karakter, hashtag hariç) ---------------------------
# Telegram video caption sınırı 1024; sistem sonradan kesmek ZORUNDA KALMAMALI.
CAPTION_ALT_SINIR = 650        # altında QA FAIL (net eşik)
CAPTION_HEDEF_MIN = 700        # hedef bant (üretici + hakem aynı bandı söyler)
CAPTION_HEDEF_MAX = 850
CAPTION_UST_SINIR = 900        # üzeri yasak (hedef dışı uzatma)

# --- Kapak başlığı (kelime) ----------------------------------------------
KAPAK_UST_MIN_KELIME = 2
KAPAK_UST_MAX_KELIME = 4       # TAMAMI BÜYÜK HARF
KAPAK_ALT_MIN_KELIME = 4
KAPAK_ALT_MAX_KELIME = 7       # kaynak 7'yi aşarsa kelime sınırından kırpılır
KAPAK_ALTERNATIF_SAYISI = 5

# --- Seslendirme ----------------------------------------------------------
SES_SURE_MIN_ORAN = 0.85       # TTS/video süresi kabul bandı (yalnız log; WAV korunur)
SES_SURE_MAX_ORAN = 1.15
VIDEO_HIZ_MIN = 0.50           # FFmpeg senkron katmanı clamp sınırları
VIDEO_HIZ_MAKS = 1.50
