# otoXtra Reels & Telegram Asistanı — Güncel Mimari Şema

> Son güncelleme: 21 Ağustos 2026
>
> Bu belge üretimdeki gerçek kod akışını, veri sözleşmelerini ve güvenlik kurallarını açıklar.

## 1. Fiziksel yapı

```text
rlstg-asist/
├── .github/workflows/
│   ├── ci.yml                         # PR/main test ve statik kontrol kapısı
│   └── telegram-video-optimized.yml   # Tek üretim workflow'u
├── cloudflare/
│   └── telegram-webhook.js            # Telegram webhook ve workflow_dispatch
├── core/
│   ├── character_profiles.py          # female=Autonoe, male=Charon
│   ├── config.py                      # API key/model listeleri ve medya ayarları
│   ├── media.py                       # FFmpeg/ffprobe, WAV ve render işlemleri
│   ├── pipeline.py                    # Ana orkestrasyon, QA ve zaman ölçümü
│   ├── prompts.py                     # Prompt yükleme ve runtime kilitleri
│   ├── prompts/*.txt                  # Rol bazlı promptlar
│   ├── router.py                      # Model/key rotasyonu ve Gemini TTS
│   ├── schemas.py                     # Gemini structured-output sözleşmeleri
│   ├── social_fallbacks.py            # Güvenli caption/Threads fallbackleri
│   └── utils.py                       # JSON ayrıştırma yardımcıları
├── duo/
│   ├── duo_strategy.py                # Kullanıcı override + AI mod normalizasyonu
│   ├── duo_script.py                  # Konuşma haritası normalizasyonu
│   ├── duo_script_engine.py           # Diyalog sözleşmesi ve doğrulama
│   └── duo_audio.py                   # Tek çağrıda multi-speaker TTS
├── telegram/
│   ├── telegram_webhook_intake.py     # Telegram videosunu runner'a indirir
│   ├── telegram_pipeline_worker.py    # Pipeline ve Telegram teslimatı
│   ├── telegram_pipeline_guard.py     # Üretim single-pass ve sosyal korumalar
│   └── telegram_pipeline_social_entry.py # Üretim workflow giriş/uyumluluk katmanı
├── tests/                             # Birim ve bütünlük testleri
├── schema/proje_yapisi.md             # Bu belge
└── requirements.txt
```

## 2. Üretim giriş akışı

```text
Telegram
  → Cloudflare Worker
  → GitHub workflow_dispatch (telegram-video-optimized.yml, ref=main)
  → Telegram videosunu indir
  → telegram.telegram_pipeline_social_entry
  → compatibility guard'ları yükle
  → telegram_pipeline_guard'ı yükle
  → telegram_pipeline_worker.main()
  → core.pipeline
  → Telegram'a final video + başlıklar + Threads gönder
```

Tek üretim yolu `telegram-video-optimized.yml` dosyasıdır. Böylece farklı guard veya runner davranışı taşıyan paralel workflow bulunmaz.

## 3. İçerik ve medya akışı

```text
1. Forensic Video Analysis
   video bytes → VIDEO_ANALYSIS_SCHEMA

2. Research / Fact Lock
   video_state → Search destekli model
   Search başarısızsa structured-output denemesi
   tüm model rotaları başarısızsa yalnızca OBSERVED verilerle güvenli fallback

3. Editorial Brain
   video_state + fact_lock + kullanıcı notu + runtime içerik türü kilidi → EDITORIAL_SCHEMA

4. Reels Creative
   editorial + fact_lock + video + runtime süre/içerik türü kilidi → REELS_CREATIVE_SCHEMA

5. Voice plan
   kullanıcı açık mod söylediyse mutlak override
   kullanıcı mod söylemediyse video/içeriğe göre AI kararı
   DUO seçildiyse konuşma haritasında female ve male zorunlu

6. DUO Script
   plan + Fact Lock → DUO_SCRIPT_SCHEMA
   speaker değerleri ve aşırı kelime sapmaları doğrulanır

7. TTS
   DUO: tek Gemini çağrısı, Autonoe + Charon
   SOLO: kullanıcı override'ı veya AI editoryal kararıyla tek prebuilt voice

8. Caption + Threads
   aynı Editorial/Fact Lock ve içerik türü sözleşmesiyle birbirinden bağımsız iki kol paralel çalışır;
   boş/artifact yanıtta Fact Lock tabanlı yerel fallback

9. QA
   Fact Lock, seçilen içerik türü, voice mode, TTS dosyası ve sosyal çıktılar doğrulanır

10. FFmpeg render
   gerçek WAV süresi ölçülür; video güvenli hız sınırları içinde senkronlanır

11. Telegram delivery
   final rapor, video, başlık seçenekleri ve Threads gönderilir
```

Telegram ilerleme arayüzü dokuz üst seviye adım gösterir. Actions logları yukarıdaki alt işlemleri ayrı ayrı ölçer.

## 4. Ses modu sözleşmesi

### 4.1 Kullanıcı override + AI editoryal karar

- Kullanıcı açıkça `DUO`, iki ses, `SOLO_FEMALE`, yalnızca kadın, `SOLO_MALE` veya yalnızca erkek isterse runtime bu talebi model kararının üstünde uygular.
- Kullanıcı ses modu belirtmezse Reels Creative modeli video, hikâye ve doğal konuşma yapısına göre `DUO`, `SOLO_FEMALE` veya `SOLO_MALE` seçer.
- İki anlamlı bakış açısı ve gerçek tercih gerilimi varsa DUO; ikinci karakter dolgu olacaksa SOLO tercih edilmesi beklenir.
- Aynı notta açık DUO/iki ses talebi bulunması, olumsuz örnek içindeki SOLO sözcüklerinin yanlış override oluşturmasını engeller.
- Geçersiz veya eksik model modu güvenli varsayılan olarak `DUO`ya düşer.

### 4.2 Karakter eşleşmesi

| Script speaker | Gemini speaker etiketi | Prebuilt voice |
|---|---|---|
| `female` | `Autonoe` | `Autonoe` |
| `male` | `Charon` | `Charon` |

### 4.3 Gemini multi-speaker wire yapısı

DUO isteği aşağıdaki iç içe yapı ile gönderilir:

```text
GenerateContentConfig
└── speech_config: SpeechConfig
    └── multi_speaker_voice_config: MultiSpeakerVoiceConfig
        └── speaker_voice_configs (tam olarak 2)
            ├── Autonoe → Autonoe
            └── Charon  → Charon
```

`MultiSpeakerVoiceConfig` doğrudan `speech_config` alanına verilmez. Böyle bir kullanım SDK tarafından sessizce `speechConfig: {}` şekline dönüşebilir ve tek ses üretimine neden olur.

### 4.4 Fail-closed kuralları

DUO üretiminde:

- Tam olarak iki farklı speaker etiketi bulunmalıdır.
- İki farklı voice bulunmalıdır.
- Script hem `female` hem `male` segment içermelidir.
- DUO TTS başarısızsa tek sesli TTS ile başarılıymış gibi devam edilmez.
- QA yalnızca gerçekten `ses_modu == DUO` ve geçerli WAV varsa DUO sesini kabul eder.

## 5. Structured-output şemaları

| Şema | Ana zorunlu alanlar |
|---|---|
| `VIDEO_ANALYSIS_SCHEMA` | `video_identity`, `kapak_ani_saniye`, `timeline`, `observed_facts`, `unknowns`, `possible_inference`, `visual_opportunities` |
| `FACT_LOCK_SCHEMA` | `facts`, `turkiye_satis_durumu` |
| `EDITORIAL_SCHEMA` | `core_story`, `why_it_matters`, `primary_facts`, `audience_trigger`, `tone` |
| `REELS_CREATIVE_SCHEMA` | `hook_families`, `secilen_aile_index`, `kapak_basliklari`, `seslendirme_metni`, `anlatim_modu`, `duo_stratejisi`, `konusma_haritasi` |
| `DUO_SCRIPT_SCHEMA` | `segments[].speaker`, `segments[].text` |
| `CAPTION_SCHEMA` | `reels_aciklamasi`, `reels_hashtagleri` |
| `THREADS_SCHEMA` | `threads_aciklamasi` |
| `QA_SCHEMA` | `tone_check`, `overall`, `regeneration_targets` |

Seçilen içerik türü (`eglence`, `dengeli`, `bilgi`, `teknik`) Telegram'dan pipeline'a taşınır ve yalnız Reels metninde değil Editorial Brain, Reels Creative, Caption, Threads ve Final QA katmanlarının tamamında aynı runtime sözleşmesi olarak kilitlenir. Pipeline sonucu ve logu uygulanan türü ayrıca kaydeder.

`anlatim_modu` ve `duo_stratejisi.uygunluk` değerleri `DUO`, `SOLO_FEMALE`, `SOLO_MALE` enum'larıyla sınırlandırılmıştır. Runtime kullanıcı override'ı model kararından üstündür; override yoksa doğrulanmış model kararı korunur.

## 6. Model ve API key rotasyonu

`SmartRouter`:

1. Her modeli yapılandırılmış API key'lerinde sırayla dener.
2. 429/kota ve 503/geçici hatalarda sıradaki key'e geçer.
3. 404 veya desteklenmeyen model/config hatasında modeli geçici blacklist'e alır.
4. Free-tier desteği olmayan key/model kombinasyonunu key bazında atlar.
5. Search rotası başarısız olduğunda Search'siz structured-output fallback'i dener.

API key değerleri loglanmaz; yalnızca `GEMINI_API_KEY_1` gibi alias'lar görünür.

## 7. Gözlemlenebilirlik

Actions loglarında:

- Her satırda UTC zaman ve pipeline başlangıcından beri geçen süre
- Her üst seviye stage için START/END ve dakika
- Her Gemini request için request numarası
- Her model/key denemesinin süresi
- Model turu ve toplam API süresi
- Forensic, Research, Editorial, Reels, DUO Script, TTS, Caption, Threads ve QA süreleri
- FFmpeg render süresi
- Telegram mesaj/video gönderim süreleri
- Pipeline sonunda timing summary ve toplam wall time
- `/usr/bin/time -v` ile CPU/RAM özeti
- GitHub Job Summary içinde QA, voice mode, warning ve error sayıları

Tam promptlar, API key değerleri ve tam kullanıcı/model metinleri Actions loguna yazılmaz. Yalnızca anahtar listesi, JSON karakter sayısı, metin karakter sayısı ve liste elemanı sayısı gibi yapısal özetler yazılır.

## 8. Fallback sınırları

- **Research:** Dış doğrulama tamamen başarısızsa yalnızca videoda gözlenen gerçeklerle devam eder; yeni iddia üretmez.
- **Caption/Threads:** Boş veya artifact çıktı Fact Lock tabanlı yerel metinle değiştirilir.
- **DUO Script:** LLM scripti başarısızsa onaylı Reels metni konuşma haritasına göre yerel olarak bölünebilir; ancak ortaya çıkan script yine iki speaker içermeli ve multi-speaker TTS'den geçmelidir.
- **DUO Audio:** Tek sese fallback yoktur.
- **Süre uyumu:** Geçerli WAV sırf ideal oran dışında diye silinmez; FFmpeg senkronuna bırakılır.

## 9. Kuyruk yaşam döngüsü

- Telegram girdisi ton seçimi beklerken `data/pending/<update_id>.json` olarak geçici tutulur.
- Workflow başarıyla dispatch edildiği anda ilgili pending dosyası silinir.
- Kullanıcı ton seçmeden kuyruğu terk ederse `created_at` alanı sayesinde 24 saatten eski kayıtlar sonraki doğrulanmış webhook isteklerinde otomatik temizlenir.
- `data/pending/*.json` Git tarafından ignore edilir; runtime kuyruk verileri kaynak kod geçmişine eklenmez.
- GitHub Actions runner'ına indirilen video ve `/tmp` medya çıktıları ephemeral runner sona erdiğinde yok olur.

## 10. Medya sözleşmesi

- TTS ham PCM: `24000 Hz`, mono, 16-bit
- TTS konuşma hızı: `1.20x`
- Final audio: `48000 Hz`, mono AAC, `192k`
- Video hızlandırma üst sınırı: `1.50x`
- Video yavaşlatma alt sınırı: `0.50x`
- Video codec: H.264, `yuv420p`
- Render timeout: 600 saniye

Kaynak medya raporu ve final medya raporu Actions loguna yazılır.

## 11. Test ve PR kapısı

PR öncesinde en az:

```bash
GEMINI_API_KEY=test python -m pytest -q
python -m compileall -q core duo telegram
node --check cloudflare/telegram-webhook.js
```

Ayrıca tüm workflow YAML dosyaları parse edilmeli ve `git diff --check` temiz olmalıdır. Gerçek Gemini/Telegram entegrasyon testi secret gerektirdiği için unit testlerden ayrı tutulur.
