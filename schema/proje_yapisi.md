# rlstg-asist — Mimari Şema (v5 · 25 Eylül 2026)

> **Bu belge AI'a proje yetkilendirmek için yazıldı.** Her dosyanın sorumluluğu, veri
> sözleşmeleri, fail-closed kurallar ve runtime akışı tek yerde. Kod tek gerçek kaynaktır;
> bu belge okuma yol haritası + dokunma kurallarıdır.

## 0. 30 saniyelik özet

Telegram'dan gelen video/metin → Cloudflare Worker (ton seçimi + GitHub dispatch) →
GitHub Actions (video indirme) → `telegram.telegram_pipeline_worker.main()` →
`core.pipeline` (Forensic → Research/Fact Lock → Editorial → Agentic 4-ajan döngüsü
(Detective→Hook→Script→Critic→TTS→Metadata) → Threads → Final QA (≤1 kontrollü
yenileme) → FFmpeg render) → Telegram'a final video + 5 kapak alternatifi + Threads.

**Monkey-patch katmanı YOK.** Sosyal korumalar, research fallback'i ve QA
non-blocking fallback'i `core/pipeline.py` içinde native uygulanır.

```
Telegram → cloudflare/telegram-webhook.js (pending kuyruk + ton butonları)
  → workflow_dispatch: telegram-video-optimized.yml (ref=main)
  → python -m telegram.telegram_webhook_intake   (yalnız video modu; stdlib-only)
  → python -m telegram.telegram_pipeline_worker  (üretim + teslimat)
```

## 1. Dosya haritası

| Dosya | Satır | Sorumluluk (tek görev) | Dışa açılan API |
|---|---|---|---|
| `core/config.py` | ~115 | API key okuma, model listeleri, timeout/bütçe, medya sabitleri, `PIPELINE_ADIMLARI` | `API_KEYS`, `*_MODELLERI`, `ISTEK_ZAMAN_ASIMI_MS`, cooldown sabitleri, `TON_*`, `model_arama_destekliyor_mu` |
| `core/schemas.py` | ~180 | Gemini structured-output sözleşmeleri (7 üretim + 5 agentic şema) | `VIDEO_ANALYSIS/FACT_LOCK/EDITORIAL/CAPTION/THREADS/QA_SCHEMA`, `DETECTIVE/HOOK_GEN/SCRIPT_WRITER/CRITIC/METADATA_GEN_SCHEMA` |
| `core/prompts.py` | ~75 | `core/prompts/*.txt` yükleme + runtime içerik-türü kilidi + kelime aralığı | `*promptunu_olustur(...)` (forensic/research/editorial/caption/threads/qa), `icerik_tonu_talimati`, `durumu_metne_donustur`, `girdi_birlestir` |
| `core/prompts/*.txt` | 533 | Rol promptları (7 dosya: forensic, research, guncellik_talimati, editorial, caption, threads_promptu, qa) | — (metin) |
| `core/router.py` | ~740 | `SmartRouter`: model×key turu, hata sınıflandırması, kota/slow/overload bütçeleri, TTS config'leri | `SmartRouter` (`metin_uret`, `video_analiz_et`, `ses_uret`, `coklu_ses_uret`, `istek_profili`, `hizli_basarisizlik` (+ geriye uyumlu `hizli_basarislik` takma adı), `yakin_zamanda_asiri_yuk_var_mi`), `guvenli_json_yukle`. İstek profili ve hızlı-başarısızlık bayrağı **thread-local** (paralel ajanlar birbirinin timeout'unu ezmez) |
| `core/pipeline.py` | ~945 | Orkestrasyon: adım fonksiyonları, sosyal korumalar, QA yenileme döngüsü, payload, iki giriş | `pipeline_calistir`, `metin_pipeline_calistir` (+ `_*` dahili) |
| `core/agentic.py` | ~750 | Agentic 4-ajan döngüsü + kelime/TTS güvenlik + TTS segment üretimi. **`core.pipeline`'ı İTHAL ETMEZ** | `agentic_icerik_uretimi(..., mod_karari=, baglam=, qa_geri_bildirimi=)`, `kapaklari_yeniden_uret`, `_run_timed`, `_coerce_positive_float`, `_beklenen_gercek_mod`, `_istege_bagli_ajan`, `_istek_profili`, `_hizli_basarislik`, `_yakin_zamanda_asiri_yuk`, `VOICE_*` sabitleri |
| `core/narration_mode.py` | ~150 | Anlatım modu AI kararı (DUO/SOLO_FEMALE/SOLO_MALE) + `ANLATIM_MODU_SCHEMA` | `anlatim_modu_karar_ver`, `ANLATIM_MODU_SCHEMA`, `GECERLI_MODLAR` |
| `core/cover_titles.py` | ~370 | [Kural: Reels Kapak Yazısı Formatı]: 5×(Üst 2-4 kelime BÜYÜK + Alt 4-7 kelime cümle) doğrulama/düzeltme/yerel tamamlama | `kapak_basliklarini_normalize_et`, `kapak_basliklarini_metne_dok`, `ust/alt_baslik_duzenle`, `ALTERNATIF_SAYISI` |
| `core/duo_audio.py` | ~65 | Karakter→voice haritası (Autonoe/Charon) + tek çağrı multi-speaker TTS (fail-closed) | `CHARACTER_VOICES`, `voice_for_character`, `duo_ses_uret`, `_duo_transcript` |
| `core/social_fallbacks.py` | ~95 | Artifact tanıma + Fact Lock tabanlı güvenli caption/threads metinleri | `looks_like_artifact`, `sanitize_hashtags`, `caption_fallback`, `threads_fallback`, `text`, `first_fact`, `model_identity`, `DEFAULT_HASHTAGS` |
| `core/media.py` | ~235 | FFmpeg/ffprobe: WAV yazma, atempo hızlandırma, süre/probe, video+TTS render | `video_ve_sesi_birlestir`, `sesi_hizlandir`, `wav_yaz`, `gecici_dosya_yolu`, `gecici_ses_yolu`, `temp_dosya_temizle`, `medya_raporu`, `video_suresini_al`, `_parse_ffmpeg_stderr` |
| `core/web_search.py` | ~185 | DuckDuckGo (`ddgs`) çoklu sorgu — sorgular **paralel** (`WEB_SEARCH_PARALLEL`, varsayılan 3), sonuç sırası korunur; paralel aşamada hata/hız sınırı/zaman aşımı alan sorgular **seri olarak bir kez daha** denenir (kapsam daralmaz); thread+process izolasyonlu timeout | `web_arastirma_yap`, `arastirma_sorgulari_olustur`, `duckduckgo_sorgu` |
| `telegram/telegram_pipeline_worker.py` | ~495 | Production giriş: Telegram mesaj/video gönderimi, ilerleme barı (`_LoadingEditor`: edit'ler arka planda, birleştirilerek; pipeline'ı bloklamaz), QA bulguları raporu, timing log, `pipeline_result.json`, teslimat öncesi son sosyal tamamlama | `main()`, `process(path)`, `process_text(text)` |
| `telegram/telegram_webhook_intake.py` | ~78 | Video modu: videoyu runner'a indirir. **stdlib-only** (workflow'ta `pip install` ÖNCE çalışmaz) | `main()`, `_safe_filename` |
| `cloudflare/telegram-webhook.js` | ~90 | Webhook: girdiyi `data/pending/<update_id>.json` yazar (GitHub Contents API), inline ton butonları, callback→`workflow_dispatch`, 24s stale cleanup | — |
| `tests/` | ~1600 |

`duo/` paketi, `core/utils.py`, `core/character_profiles.py`, monkey-patch dosyaları
(`telegram_pipeline_guard.py`, `telegram_pipeline_social_entry.py`) ve
`reels_creative_prompt.txt` + `REELS_CREATIVE_SCHEMA` **kaldırıldı** (üretimde ölüydü;
gerekli davranışlar yukarıdaki dosyalara native taşındı).

## 2. Üretim akışı (adım → girdi/çıktı → düşme davranışı)

| # | Adım | Şema | Profiller | API düştüğünde |
|---|---|---|---|---|
| 1 | Forensic video analizi | `VIDEO_ANALYSIS_SCHEMA` | `video` (120s) | **Zorunlu** — exception pipeline'ı durdurur |
| 2 | Research/Fact Lock (DDGS + Gemini, `arama_kullan=False`; DDGS katmanı `web_search.py` içinde) | `FACT_LOCK_SCHEMA` | `uzun_metin` (60s) | **Fallback:** yalnız OBSERVED gerçeklerle Fact Lock (`forensic-fallback`); yeni iddia üretilmez |
| 3 | Editorial Brain (+ `_runtime_priority_audit`: seçilen index vs en yüksek `toplam_oncelik` → `aligned`/`review`) | `EDITORIAL_SCHEMA` | `uzun_metin` | **Zorunlu** |
| 4a | Anlatım modu kararı (isteğe bağlı; kullanıcı notunda açık mod varsa atlanır; aşırı yükte de denenir, router tekrarlarıyla). **Arka planda** çalışır (`_anlatim_modu_arka_planda`); Detective/Hook onunla paralel ilerler, mod yalnız Script Writer'dan hemen önce beklenir | `ANLATIM_MODU_SCHEMA` | `istege_bagli` (30s + 60s bütçe) | {} → mod varsayılan DUO |
| 4b | **Agentic döngü** (bkz. §3) | — | — | Script Writer zorunlu; diğerleri güvenli varsayılanla |
| 4c | Metadata→Caption eşleme (Metadata ajanı TTS ile **paralel** çalışır) + eksikse Caption ajanı (sosyal korumalı, §6) | `METADATA_GEN_SCHEMA`/`CAPTION_SCHEMA` | `metin` | Fallback: Fact Lock caption + `DEFAULT_HASHTAGS` |
| 4d | Threads (sosyal korumalı, §6) — agentic döngü ile **paralel** arka planda başlar | `THREADS_SCHEMA` | `metin` | Fallback: Fact Lock threads |
| 5 | Final QA | `QA_SCHEMA` | `uzun_metin` | `qa_unavailable: True` + PASS (yapısal kontroller yine çalışır) |
| 6 | FFmpeg render (yalnız video modu; metin modu ses dosyasını payload'da bırakır) | — | — | `final_video=""` → worker "final video üretilemedi" ile FAIL |

`pipeline_calistir` payload anahtarları (worker bunları okur):
`mode, seslendirme_metni, reels_aciklamasi, reels_hashtagleri, kapak_basliklari[],
threads_aciklamasi, ses_basarili, ses_dosyasi, secilen_ses_ingilizce,
kullanilan_{metin,ses,threads}_modeli, ses_modu, ses_modu_sesi, qa_regeneration_rounds,
final_video, temp_input_video, fact_lock, editorial_brief, duo_plan, duo_script,
qa_result, qa_pass, content_tone, pipeline_state{...}, input_media?, output_media?`

### QA yenileme döngüsü (`_qa_regeneration_loop`)
- **Hedef çözümleme (`_qa_sonucunu_coz`)** — model çıktısı ham kullanılmaz:
  - `overall` serbest yazımda da okunur (`"FAIL: ..."`, `"pass"`).
  - Takma adlar `QA_TARGET_ALIASES` ile desteklenen hedefe eşlenir: `FACT_FAIL`/`MODEL_FAIL`/
    `VIDEO_FAIL`/`CURRENT_DATA_FAIL` → kapsamı gerekçeden çözülen FACT (`_fact_kapsami`:
    caption/threads geçiyorsa onlar, belirsizse **seslendirme**); `HOOK/LENGTH/TTS/TONE/...` →
    `VOICEOVER_FAIL`; `HASHTAG_FAIL` → `CAPTION_FAIL`; `VISUAL_MATCH_FAIL` → `COVER_FAIL`.
  - `overall=FAIL` ama hedef listesi boş/tanınmıyorsa FAIL veren tekil `*_check`
    alanlarından türetilir (`QA_CHECK_TARGETS`).
  - `length_check` FAIL yalnız deterministik ölçüm KESİN uygunluk gösterirse yok
    sayılır (`length_check_override`): kelime sayısı SIKI aralıkta (hedef ±%10) VE
    gerçek TTS/video oranı 0.85–1.15. Gevşek ±%20 tolerans burada kullanılmaz.
  - Loga yalnız FAIL veren kontrol **adları** yazılır (gerekçe metni değil, §10).
    Gerekçeler Script Writer'a `qa_geri_bildirimi` olarak ve Telegram raporuna gider.
  - Hiç hedef çıkarılamazsa bu açıkça loglanır ve render durdurulur (sessiz FAIL yok).
- Maks **1** seslendirme/video yenilemesi (`MAX_QA_REGEN`). Hedef→etki:
  - `VOICEOVER_FAIL|DUO_SCRIPT_FAIL` → Script Writer + **Critic** + TTS (+ paralel
    Metadata) QA geri bildirimiyle yeniden. Critic QA gerekçelerini bilir, düzeltmeleri
    geri aldırmaz. **Başarılı** Detective/Hook önceki turdan yeniden kullanılır
    (`baglam`); ilk turda düşmüş olanlar yeniden denenir.
  - Hook, QA şunlardan birini işaretlediyse geri bildirimle **yeniden üretilir**:
    `COVER_FAIL`, `hook_check`/`HOOK_FAIL` ya da herhangi bir gerçeklik hatası
    (`FACT_FAIL`, `fact_check`…). Sebep: kanca Script Writer'a girdi olur ve
    doğrulanmamış iddiayı senaryoya geri taşıyabilir.
  - **Yalnız `COVER_FAIL`** → `kapaklari_yeniden_uret` (Script/TTS yok; açılış kancası korunur).
  - `CAPTION_FAIL` → Caption ajanı QA gerekçeleri + güncel seslendirmeyle yeniden yazar
    (seslendirme yenilense de Metadata'nın geri bildirimsiz caption'ı kullanılmaz).
    `THREADS_FAIL` → Threads QA gerekçeleriyle.
  - Yenileme düşerse (API/TTS) önceki **model** çıktısı korunur; model çıktısı
    yerel şablon/fallback ile ezilmez (Hook, kapak, caption, threads, TTS).
- **Sosyal ek tur (`SOCIAL_QA_EXTRA_REGEN=1`):** yenileme sonrası kalan hedefler
  yalnız sosyal katmansa (caption/threads/kapak) video yeniden üretilmeden bir
  düzeltme turu + QA daha yapılır.
- **DUO fail-closed:** `expected_mode == DUO` iken `ses_modu != DUO` veya WAV yoksa
  PASS bile olsa `DUO_SCRIPT_FAIL` → FAIL.
- **Non-blocking (`_nonblocking_qa_mi`):** sosyal ek turdan sonra da kalan hedefler yalnız
  videoyu değiştirmeyen katmanlarsa (`CAPTION_FAIL`/`THREADS_FAIL`/`COVER_FAIL`) ve/veya
  `DUO_SCRIPT_FAIL` + geçerli **DUO** WAV ise → `qa_pass=True`, `nonblocking_targets`
  (+ `social_nonblocking_fallback` / `duo_nonblocking_fallback`) işaretlenir; worker
  bunları raporda uyarı olarak gösterir. `VOICEOVER_FAIL` (gerçeklik dahil) fail-closed kalır.
- Anlatım modu kararı döngüye `editorial_hazirlayici` (future `.result`) ile tembel verilir.
- Dönüş **15'li tuple, sabit sıralama**:
  `(reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli,
  ses_modu, ses_dosyasi, caption_state, threads_state, qa_state, qa_rounds,
  model_caption, model_threads, qa_pass)` — bu sırayı bozma; çağıranlar pozisyonel unpack eder.

## 3. Agentic döngü (`core/agentic.py :: agentic_icerik_uretimi`)

Sıra: **Detective → Hook → [mod kararı beklenir] → Script Writer → Critic → TTS ∥ Metadata**
(tek `for deneme in range(VOICE_REGEN_MAX+1)` döngüsü içinde; Detective/Hook döngü başında
bir kez). `baglam` sözlüğü Detective/Hook çıktısını ve başarı bayraklarını
(`detective_basarili`, `hook_ajan_basarili`, `hook_yenile`) QA yenilemesi için saklar.
`qa_geri_bildirimi` Script Writer'a ve Critic'e verilir. Dönüş 9'lu tuple (son öğe metadata).

**Kapak kurtarma:** Hook ajanı (router tekrarlarına rağmen) düşerse kapaklar yerel
setle hazırlanır. Hook ajanı Script + Critic + TTS ile **paralel** bir kez daha denenir;
yanıt verirse kapak başlıkları model çıktısıyla değiştirilir. Seslendirme açılışı
değişmez; kritik yolda bekleme olmaz.

| Ajan | Şema | Zorunluluk | Düşerse |
|---|---|---|---|
| Detective | `DETECTIVE_SCHEMA` | isteğe bağlı (aşırı yükte de denenir) | `{}` (QA yenilemesinde yeniden denenir) |
| Hook Gen | `HOOK_GEN_SCHEMA` (+`KAPAK_FORMAT_KURALI`) | isteğe bağlı | `_hook_fallback(editorial)` + paralel kapak kurtarma |
| Script Writer | `SCRIPT_WRITER_SCHEMA` | **ZORUNLU** (router overload tekrarlarına rağmen düşerse exception yukarı) | — |
| Critic | `CRITIC_SCHEMA` | isteğe bağlı (QA yenilemesinde de çalışır) | onaylı sayılır; onay vermezse **1 revize** (boş revize eski scripti korur) |
| Metadata | `METADATA_GEN_SCHEMA` | isteğe bağlı | `{}` → caption Caption ajanıyla tamamlanır |

**Kapak kuralı (istisnasız):** Hook çıktısı `kapak_basliklarini_normalize_et` ile
doğrulanır; eksikse Editorial/Detective kanıtları + şablon havuzuyla **5'e tamamlanır**.
Her öğe `{ust, alt, ana(==ust), kaynak}`; `ana` worker uyumluluğu için.
Alt başlık **asla kelime sayısına kırpılmaz** (Türkçe SOV: yüklem düşer). Yerel
tamamlamada kaynaktan yalnız doğal 4-7 kelimelik cümle/yan cümle alınır, yoksa şablon;
modelden gelen kırpık sonlar (`_kirpik_sonu_temizle`) temizlenir.

**DUO diyalog kuralları** (Script Writer promptu içinde, QA `duo_check` bunları denetler):
HOOK→FRICTION→PROOF→REVERSAL→PAYOFF/CALLBACK omurgası, 2. turdan itibaren lexical
uptake, asimetrik ritim (eşit uzunluk/mekanik salınım yasak), callback kapanış.
**Atışma + kışkırtma + konuşma tepkileri (Eylül 2026 monotonluk düzeltmesi):**
karşılıklı atışma/iğneleme, arada seyirciyi kışkırtan iddialı cümleler, kısa tepki
replikleri ('Oha!', 'Bir saniye ya!') ve bazen aynı konuşmacının 2-3 tur üst üste
konuşması SERBEST ve istenen; cümle bütçesi ortalama 9-13 kelime + 1-5 kelimelik
kısa tepki cümleleri karışımı. Her yazılan repliğe `tts_tag` zorunlu (boş bırakma).
SOLO'da: hook→bilgi→dönüş→callback; diyalog kalıbı yasak.

**TTS segment üretimi:** `segments[].text` yalnız duyulacak sözlerdir.
`tts_tag` (`[vurgulu]`, `[alaycı]`, `[savunarak]`...) transkripte yazılmaz;
`core/tts_delivery.py` onu İngilizce prosodi notuna çevirir ve router bu notu
`#### TRANSCRIPT` sınırının üstünde gönderir. `gemini-2.5-flash-preview-tts`
köşeli etiketi kelime diye okuduğu için etiket metne eklenmez.
`yorum_tetikleyici_soru` sona vurgu stiliyle, etiketsiz eklenir (DUO'da son
konuşmacının karşıtı, SOLO'da tek speaker). `duo_script = {status: ready|fallback,
segments:[{speaker,text,style}], contract:{mode}, conversation_design:{}, model:"agentic"}`.

**Güvenlik duvarları (döngü içi):**
- Boş segment → yeniden yazım (≤`VOICE_REGEN_MAX=2`).
- Kelime sayısı: `hedef = sure × KELIME_HIZI_ORANI(2.9)` 5'e yuvarlak; aralık ±%10.
  Sapma **`VOICE_WORD_TOLERANCE_RATIO=0.20`'den azsa yeniden yazım ATLANIR** (FFmpeg
  senkron kapatır).
- TTS/video süre oranı (0.85–1.15) **diagnostiktir**: oran dışı WAV silinmez, render'a bırakılır.
- DUO TTS başarısızsa SOLO'ya düşülmez (fail-closed, `core/duo_audio.py` + `_duo_ses_veya_legacy_uret`).
- `reels_state` her turda: `seslendirme_metni, kapak_basliklari[5], kapak_format,
  hook_families[1], turkiye_ilgi_kancasi` (Fact Lock'taki en yüksek `onem_puani`'li
  sinyalin `guvenli_anlatim`), `metadata`.

## 4. Ses modu sözleşmesi

**Karar hiyerarşisi (üst→alt):**
1. Kullanıcı notunda açık mod (`_explicit_voice_mode_from_notes`: "duo/iki sesli",
   "solo/tek ses", "yalnızca kadın/erkek" + olumsuzluk "olmasın" ayrımı; "sen seç" ifadesi
   kararı AI'a bırakır). Video modda not = video caption; **metin modda not = kullanıcı metnin kendisi.**
2. `ANLATIM_MODU_ZORLA` env (test/operasyon).
3. AI kararı (`anlatim_modu_karar_ver`, 4a adımı) → `editorial["anlatim_modu_karari"]`.
4. Varsayılan: **DUO**.

**Karakter→voice (tek kaynak `core/duo_audio.py`):** `female→Autonoe`, `male→Charon`
(hem Gemini speaker etiketi hem prebuilt voice aynı ad).

**Multi-speaker wire (kritik):** `SpeechConfig.multi_speaker_voice_config =
MultiSpeakerVoiceConfig(speaker_voice_configs=[Autonoe→Autonoe, Charon→Charon])`
tam olarak 2, farklı etiket+ses. `MultiSpeakerVoiceConfig`'i `speech_config`'e
DOĞRUDAN vererek boş `speechConfig:{}` (tek ses) oluşturma.

## 5. Structured-output şemaları (zorunlu alanlar)

| Şema | Zorunlu alanlar |
|---|---|
| `VIDEO_ANALYSIS_SCHEMA` | `video_identity{brand, exact_model, confidence}`, `kapak_ani_saniye`, `timeline[{olay,...}]`, `observed_facts`, `unknowns`, `possible_inference`, `visual_opportunities` |
| `FACT_LOCK_SCHEMA` | `facts[{fact, status∈OBSERVED/VERIFIED/INFERENCE/UNKNOWN/CONTRADICTED, source?}]`, `turkiye_satis_durumu∈VAR/YOK/BILINMIYOR`, `turkiye_ilgi_sinyalleri[{kategori, bulgu, neden_turkiyede_ilginc, guvenli_anlatim, onem_puani 0-10}]` |
| `EDITORIAL_SCHEMA` | `story_options[{isim,kategori,fact_strength,turkish_audience_relevance,economic_or_practical_impact,surprise_gap,visual_support,shareability,repetition_risk,toplam_oncelik,dayanak}]`, `core_story`, `selected_story_index`, `selected_story_category`, `selection_rationale`, `why_it_matters`, `primary_facts`, `audience_trigger`, `tone` |
| `CAPTION_SCHEMA` | `reels_aciklamasi`, `reels_hashtagleri[5]` |
| `THREADS_SCHEMA` | `threads_aciklamasi` (≤500 kr, soru/hashtag yok) |
| `QA_SCHEMA` | `tone_check`, `viral_priority_check`, `overall`, `regeneration_targets[]` (diğer check alanları isteğe bağlı) |
| `ANLATIM_MODU_SCHEMA` | `anlatim_modu∈DUO/SOLO_FEMALE/SOLO_MALE`, `duo_katma_degeri`, `solo_katma_degeri`, `guven`, `gerekce` |
| `DETECTIVE_SCHEMA` | `kronik_sikayetler`, `turkiye_ozel_magduriyet`, `viral_kan_mali` |
| `HOOK_GEN_SCHEMA` | `secilen_sablon`, `kapak_basliklari[5×{ust,alt}]`, `kapak_metni`, `ilk_3_saniye_kanca` |
| `SCRIPT_WRITER_SCHEMA` | `segments[{speaker, tts_tag, text}]`, `yorum_tetikleyici_soru` |
| `CRITIC_SCHEMA` | `score 1-10`, `approved`, `feedback` |
| `METADATA_GEN_SCHEMA` | `reels_baslik`, `reels_aciklama`, `reels_hashtag` |

**İçerik türü kilidi** (`eglence|dengeli|bilgi|teknik`): Telegram butonu → workflow
input → `state.content_tone` → editorial/caption/threads/qa promptlarına runtime kilidi
olarak eklenir; bilinmeyen değer `dengeli`ye düşer.

**Türkiye fiyat kuralı:** Global fiyat pazar/para birimi bağlamıyla kullanılabilir;
kesin Türkiye fiyatı/ÖTV UYDURMAZ; `turkiye_satis_durumu` mutlaka taşınır.

## 6. Sosyal koruma katmanı (`core/pipeline.py` + `core/social_fallbacks.py`)

- `_caption_calistir` / `_threads_calistir` her üretimde doğrular: metin boş değil +
  `looks_like_artifact` değil (`/tmp/`, `data/`, `.wav/.mp4...` gibi) + caption'da
  hashtag listesi dolu. Geçmezse **1 kontrollü yeniden üretim**, sonra Fact Lock
  tabanlı güvenli fallback (`model="local-fallback"`).
- Worker (`_ensure_social_outputs`) teslimattan önce BOŞ çıktıları aynı fallback'lerle
  son kez tamamlar (artifact kontrolü pipeline'dadır).
- Fallback metinler "güvenli şablon"dur; QA bunları geçerli caption saymaz
  (QA promptu bunu açıkça söyler) — ama teslimat asla boş gitmez.

## 7. Router kuralları (`SmartRouter`)

- **Tur mantığı:** her model, her key'de TAM 1 kez, seri, bekleme yok. Tam tur (tüm
  key'ler) düşmeden sonraki modele geçilmez. Geçici hatanın adımlar arası hafızası yok.
- **Kalıcı yasaklar** (adım boyunca): `404/not_found` ve `400/unsupported` → model
  blacklist (`*+model`, 24h); `limit: 0` → key blacklist (`mail+model`, 7 gün);
  `PerDay` kotası → key+model bu çalışma boyunca atlanır (Search araçlı isteklerde değil).
- **429/dakikalık kota, 503, timeout:** GEÇICI — bekleme/yasak yok, sonraki key.
- **Yavaş model:** ≥20sn süren hatalı deneme → model 3 dk sona atılır (silinmez);
  aynı modelde 2 yavaş deneme → kalan key'ler atlanır.
- **Aşırı-yük koruması:** tüm model+key geçici hatayla düşerse 15/30/45sn bekleyip tam
  tur ≤`OVERLOAD_RETRY_ROUNDS=2` kez. Toplam bekleme bütçesi 180sn; çalışma
  süresi + bekleme > 12dk ise beklenmez. `hizli_basarisizlik()` (takma ad
  `hizli_basarislik`) bloklarında bekleme YOK; bayrak thread-local'dir.
- **İsteğe bağlı ajanlar kalite önceliklidir (varsayılan):** her zaman denenir ve
  aşırı-yük tekrarlarından yararlanır. Beklemeleri **ayrı havuzdan** düşülür
  (`ROUTER_OPTIONAL_OVERLOAD_WAIT_BUDGET`, 120sn); zorunlu adımların 180sn bütçesi
  korunur. Asılı istekler `istege_bagli` profiliyle sınırlıdır (30s timeout, 120s istek
  bütçesi; bütçe bekleme sonrasına yetmiyorsa boşuna uyunmaz).
- **Hız-öncelikli operasyon anahtarları (varsayılan KAPALI):**
  `ISTEGE_BAGLI_HIZLI_BASARISIZLIK=1` → isteğe bağlı ajanlar beklemesiz tek turla
  vazgeçer. `ISTEGE_BAGLI_ASIRI_YUK_ATLA=1` → 90sn aşırı-yük penceresinde
  (`yakin_zamanda_asiri_yuk_var_mi`) hiç denenmez.
- **İstek profilleri** (`istek_profili(...)`): `metin` 45s · `uzun_metin` 60s ·
  `istege_bagli` 30s + 120sn toplam bütçe (dolunca kalan kombinasyonlar atlanır) ·
  `video` 120s · `tts` 60s. Env ile ezilebilir (`ROUTER_*`).
- **Research fallback rotası:** `metin_uret(arama_kullan=True)` JSON parse edemezse →
  Search'siz structured-output denemesi; o da düşerse `arama_kullan=False` fallback.
- API key değerleri loglanmaz; yalnız `GEMINI_API_KEY_N` alias'ları.

## 8. Medya sözleşmesi

- TTS ham PCM 24kHz/mono/16bit → atempo **1.20×** (`SES_HIZ_CARPANI`) → render'da AAC.
- Final: 48kHz mono AAC 192k; video H.264 `veryfast` CRF20 `yuv420p`, kaynak FPS korunur.
- Senkron: `hedef = ceil(ses_suresi)`; `video_hiz = video_sure / hedef`
  **0.50×–1.50×** aralığa kırpılır; süre oranı (0.85–1.15) yalnız log, WAV'i korur.
- Render timeout 600sn. Kalite (unsharp/lanczos) filtresi kasıtlı olarak YOK.
- Süre okuma: ffprobe → yoksa `ffmpeg -i` stderr parse (`_parse_ffmpeg_stderr`).

## 9. Kuyruk yaşam döngüsü (Telegram/Cloudflare)

- Girdi `data/pending/<update_id>.json` olarak GitHub'da tutulur
  (`{file_id, chat_id, filename, video_note, text_input, input_type, created_at}`).
- Ton butonu callback → `workflow_dispatch(telegram-video-optimized.yml, ref=main)` +
  pending dosyası silinir. Dispatch başarısızsa dosya kalır (retry mümkün).
- Her doğrulanmış webhook isteğinde 24s+ eski pending'lar temizlenir.
- `data/pending/*.json` gitignore'da — **asla commit etme**.
- Concurrency: `telegram-video-${chat_id}`, `cancel-in-progress: true`. Job 30dk.

## 10. Loglama ve gizlilik

- Her log satırı: `[UTC ms] [+job_süresi] mesaj`. Stage `📊 STAGE START/END`, adım
  `⏱️ START/END/FAIL | süre`, API istekleri numaralandırılmış.
- Model çıktıları loga **yazılmaz**: yalnız yapısal özet (`_safe_result_summary`:
  anahtar listesi, json/metin karakter sayısı, liste boyutu + model adı).
- API key değeri, tam prompt, tam kullanıcı/model metni ASLA loglanmaz.
- Son: `PIPELINE TIMING SUMMARY` + wall time; workflow `Job Summary`'e
  `pipeline_result.json` özeti (qa_pass, rounds, voice_mode, warning/error sayısı).

## 11. Test ve PR kapısı

```bash
GEMINI_API_KEY=test python -m pytest -q
python -m compileall -q core telegram tests
python -m ruff check core telegram tests --select F,E9   # fatal lint
node --check cloudflare/telegram-webhook.js
# + workflow YAML parse (ci.yml)
```
- Router/agentic testleri ağ YAPMAZ: sahte `genai.Client`/`metin_uret` istemcileri.
- Agentic testleri `core.agentic.*` hedeflerini patch'ler (modül taşınsa bile
  `core.agentic` isim uzayı sabittir).
- Gerçek Gemini/Telegram entegrasyonu secret gerektirir; CI'da YOK.

## 12. AI'a dokunma kuralları (en önemli bölüm)

1. **Monkey-patch YOKTUR** ve yeniden eklenmesin. Davranış değişikliği ilgili
   fonksiyona (pipeline/adım/ajant) native yapılır.
2. `_qa_regeneration_loop` 15'liuplü dönüş SIRASI sözleşmedir (bkz. §2); değiştirmen
   gerekiyorsa önce `pipeline_calistir` + `metin_pipeline_calistir` unpack'lerini güncelle.
3. `core/agentic.py` → `core/pipeline.py` importu EKLEME (döngü); tersi serbest.
4. TTS fail-closed zinciri korunur: DUO'da tek-ses fallback, SOLO'da DUO fallback YOK.
5. Kapak kuralı tek yerden: `core/cover_titles.py`; prompt/şemada kopya kurallama.
6. Karakter→voice tek yerden: `core/duo_audio.py CHARACTER_VOICES`.
7. Fallback metinleri tek yerden: `core/social_fallbacks.py`.
8. `telegram_webhook_intake.py` **stdlib-only** kalmalı (workflow'ta bağımlılıklar
   kurulmadan çalışır).
9. Yeni model/model sırası: `core/config.py` listeleri (kanıtlı model başta).
10. Log'a model içeriği yazma kuralı (§10) — yeni log satırlarında da geçerli.
11. Yeni şema alanı: `core/schemas.py` + ilgili `core/prompts/*.txt` birlikte güncellenir.
12. Test adımları: davranış değişiyorsa `tests/` içinde ilgili regresyon dosyasını
    güncelle; ağ bağımlılığı ekleme.
