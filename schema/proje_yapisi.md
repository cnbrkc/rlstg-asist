# otoXtra Reels & Telegram Asistanı Proje Yapısı ve Mimari Şeması

Bu döküman, projenin daha derli toplu, sürdürülebilir ve hatasız çalışmasını sağlamak amacıyla yeniden düzenlenen fiziksel ve mantıksal yapısını detaylandırmaktadır. 

Mevcut sistemin tüm işlevselliği, GitHub Actions iş akışları, Cloudflare Worker tetikleyicileri ve birim testler eksiksiz bir şekilde korunarak **Core (Çekirdek)**, **Duo (İki Sesli Anlatım)** ve **Telegram Entegrasyonu** katmanlarına ayrıştırılmıştır.

---

## 📁 1. Proje Klasör Şeması

```text
rlstg-asist/
├── .github/
│   └── workflows/
│       ├── telegram-video.yml               # Standart video pipeline iş akışı
│       └── telegram-video-optimized.yml     # Optimize edilmiş hızlı sparse-checkout iş akışı
├── cloudflare/
│   └── telegram-webhook.js                 # Cloudflare Worker Telegram webhook kodları
├── core/                                   # Çekirdek İş Mantığı Katmanı
│   ├── __init__.py
│   ├── character_profiles.py               # Karakter profilleri ve ses eşleştirmeleri
│   ├── config.py                           # Sistem yapılandırmaları ve model listeleri
│   ├── media.py                            # FFmpeg tabanlı ses/video işleme araçları
│   ├── pipeline.py                         # Master Pipeline orkestratörü
│   ├── prompts.py                          # Prompt birleştirme ve şablon doldurucu
│   ├── router.py                           # Gemini API anahtar rotasyonu ve SmartRouter
│   ├── schemas.py                          # LLM çıktıları için JSON şemaları
│   ├── social_fallbacks.py                 # Yedek sosyal medya metin sağlayıcıları
│   ├── utils.py                            # Güvenli JSON ayıklama yardımcıları
│   └── prompts/                            # Rol tabanlı ham sistem prompt metinleri
│       ├── caption_prompt.txt
│       ├── editorial_prompt.txt
│       ├── forensic_analysis_prompt.txt
│       ├── guncellik_talimati.txt
│       ├── kurallar.txt
│       ├── qa_prompt.txt
│       ├── reels_creative_prompt.txt
│       ├── research_prompt.txt
│       ├── sistem_talimati.txt
│       ├── threads_promptu.txt
│       └── video_analiz_promptu.txt
├── duo/                                    # İki Karakterli (Duo) Anlatım Katmanı
│   ├── __init__.py
│   ├── duo_audio.py                        # Çoklu ses TTS üretim koordinatörü
│   ├── duo_script.py                       # Konuşma haritası normalizasyonu
│   ├── duo_script_engine.py                # Duo Script sözleşme ve üretim şablonu
│   └── duo_strategy.py                     # Duo anlatım strateji kararları ve ağırlık yönetimi
├── telegram/                               # Telegram Bot & Runtime Entegrasyon Katmanı
│   ├── __init__.py
│   ├── telegram_pipeline_guard.py          # Telegram botuna özel tek geçişli üretim ve lokal bölme korumaları
│   ├── telegram_pipeline_runner.py         # Polling/Worker tetikleyici giriş noktası
│   ├── telegram_pipeline_social_entry.py   # Optimize GitHub Actions giriş noktası ve hata tolerans katmanları
│   ├── telegram_pipeline_worker.py         # Telegram mesaj ve video indirme işleyicisi, bildirim yöneticisi
│   └── telegram_webhook_intake.py          # GitHub runner üzerinde video indirme betiği
├── tests/                                  # Test Katmanı
│   ├── test_duo_audio.py                   # Duo ses transkript testi
│   ├── test_duo_layers.py                  # Duo normalizasyon katmanı testleri
│   ├── test_duo_script_engine.py           # Duo sözleşme ve şema doğrulama testleri
│   └── test_duo_integrity.py               # Duo uçtan uca bütünlük testleri
├── data/                                   # Durumsal Veri ve Kuyruk Depolama Alanı
│   ├── pending/                            # Bekleyen Telegram mesaj durum json dosyaları
│   └── telegram_offset.txt                 # Polling için okunan telegram offset kaydı
├── requirements.txt                        # Bağımlılık paket listesi
└── schema/
    └── proje_yapisi.md                     # Mimari dokümantasyon (Bu dosya)
```

---

## ⚙️ 2. Klasörler ve Dosya Sorumlulukları

### 🔹 2.1. `core/` (Çekirdek Katman)
Tüm sistemin ortak iş mantığını, LLM iletişimini ve video/ses montaj işlemlerini barındırır.

*   **`config.py`**: Model listelerini (Gemini 3.x Flash/Pro/TTS), hız çarpanlarını, FFmpeg CRF ve kodek ayarlarını, API anahtarı yükleme mantığını barındırır.
*   **`schemas.py`**: LLM'lerin çıktılarını doğrulamak için kullanılan JSON şemalarını (Forensic, Fact Lock, Editorial, Reels Creative, QA, vb.) barındırır.
*   **`utils.py`**: LLM'lerden gelen markdown kod bloklu yanıtları temizleyen ve güvenli bir şekilde JSON objesine dönüştüren `guvenli_json_yukle` metodunu içerir.
*   **`router.py`**: **`SmartRouter`** sınıfını barındırır. Kota aşımı (429), sunucu hatası (503) gibi durumlarda otomatik olarak API anahtarları arasında rotasyon (key-rotation) yapar. TTS ve çoklu ses üretim isteklerini yönetir.
*   **`media.py`**: FFmpeg entegrasyonudur. Ses hızlandırma, ses-video birleştirme, çözünürlük ölçekleme, video süresi okuma işlemlerini alt süreçler (`subprocess`) aracılığıyla yönetir.
*   **`prompts.py`**: `core/prompts/` altındaki metin şablonlarını okur; video süresi, kelime sayısı limitleri ve kullanıcı notları gibi dinamik değişkenleri şablona enjekte eder.
*   **`character_profiles.py`**: Karakter ses profillerini (`female -> Autonoe`, `male -> Charon`) merkezileştirir.
*   **`social_fallbacks.py`**: Üretici modellerin boş veya hatalı metin dönmesi durumunda, Fact Lock ve Editorial verilerinden yola çıkarak Instagram Caption, Hashtag ve Threads metinlerini kural tabanlı olarak üreten yedek (fallback) mekanizmasıdır.
*   **`pipeline.py`**: Master Pipeline orkestratörüdür. 9 adımlı Reels üretim sürecini sırasıyla çalıştırır.

---

### 🔹 2.2. `duo/` (İki Karakterli Anlatım Katmanı)
Platformun en güçlü özelliklerinden olan "Eş/Partner Diyaloğu" tarzındaki iki sesli Reels üretimi bu katmanda yönetilir.

*   **`duo_strategy.py`**: Reels Creative aşamasından gelen anlatım modunu (DUO, SOLO_FEMALE, SOLO_MALE), karakterlerin ağırlıklarını ve diyalog parametrelerini doğrular ve filtreler.
*   **`duo_script.py`**: Konuşma haritasını temizler ve sıralar. Tek sesli haritaları gerekirse iki sesli şablonlara otomatik tamir eder.
*   **`duo_script_engine.py`**: LLM'e gönderilecek olan diyalog yazma sözleşmesini (`contract`) hazırlar, promptu kurar ve LLM'den dönen çoklu ses konuşmalarını denetler.
*   **`duo_audio.py`**: Doğrulanmış segmentleri tek bir transkript dosyasında birleştirir, karakterlere uygun duygusal vurgu etiketleri (`[curious]`, `[confident]`) ekler ve SmartRouter üzerinden çoklu ses TTS modelini tetikler.

---

### 🔹 2.3. `telegram/` (Telegram Bot & Runner Katmanı)
Kullanıcılardan gelen girdileri alan, işlemleri başlatan ve üretilen medyayı Telegram üzerinden geri gönderen entegrasyon katmanıdır.

*   **`telegram_webhook_intake.py`**: Telegram API'sinden ilgili `file_id` değerini sorgulayarak videoyu GitHub Actions sunucusuna indirir.
*   **`telegram_pipeline_worker.py`**: Telegram botunun durum güncelleme mesajlarını yollar, pipeline'ı tetikler, üretilen video ve sosyal metinleri Telegram kanalına yükler.
*   **`telegram_pipeline_guard.py`**: Canlı ortamda aşırı API maliyeti ve gecikmeyi önlemek için Reels ve TTS aşamalarını tek geçişle (single-pass) sınırlandırır. Konuşma metnini yapay zeka yerine yerel olarak cümle sınırlarından iki karaktere bölerek hızı optimize eder.
*   **`telegram_pipeline_social_entry.py`**: GitHub Actions tarafından doğrudan çalıştırılan optimize edilmiş ana giriş noktasıdır. Çekirdek pipeline metotlarını (QA, Research, TTS, vb.) monkey-patch tekniğiyle yamalayarak, Gemini veya Duo katmanındaki geçici hataların üretimi durdurmasını engeller ve yedekleri devreye sokar.
*   **`telegram_pipeline_runner.py`**: Botun local polling modunda çalıştırılabilmesi için worker modülünü başlatan tetikleyicidir.

---

### 🔹 2.4. `tests/` (Test Katmanı)
Sistemin bütünlüğünü ve diyalog motorunu doğrulayan birim test dosyalarını barındırır.

*   **`test_duo_audio.py`**: Karakter etiket döngülerini ve transkript şablonlama kurallarını test eder.
*   **`test_duo_layers.py`**: Strateji normalizasyonunun ve konuşma haritasının solo/duo filtreleme mantığını doğrular.
*   **`test_duo_script_engine.py`**: Nesne sözleşmesinin doğru kurulduğunu ve diyalog şemalarını denetler.
*   **`test_duo_integrity.py`**: Duo katmanının uçtan uca (eğri ve eksik haritaların tamiri dahil) bütünlük testlerini yapar.

---

## 🔄 3. Veri Akış Şeması (Data Flow)

Bir kullanıcı Telegram botuna bir araç inceleme videosu ve açıklama notu gönderdiğinde sırasıyla şu adımlar gerçekleşir:

```text
[Kullanıcı] ──> (Telegram Bot) ──> [Cloudflare Worker]
                                         │
                                         ├──> 1. data/pending/ içinde JSON oluşturur
                                         └──> 2. GitHub Actions "workflow_dispatch" tetikler
                                                     │
                                             [GitHub Runner]
                                                     │
                                                     ├──> telegram_webhook_intake.py (Videoyu indirir)
                                                     └──> telegram_pipeline_social_entry.py
                                                                  │
  ┌───────────────────────────────────────────────────────────────┘
  ▼
[MASTER PIPELINE BAŞLAR] (core/pipeline.py)
  │
  ├──> Step 1: Forensic Video Analizi ──> (Gemini Video Model) ──> Araç marka/model/detay saptama
  ├──> Step 2: Research & Fact Lock ──> (Gemini Search Model) ──> Türkiye fiyatı/satış durumu sorgulama
  ├──> Step 3: Editorial Brain ──> (Gemini Text Model) ──> Sosyal medya için en vurucu kanca (hook) tespiti
  ├──> Step 4: Reels Creative ──> Anlatım stratejisi (Duo/Solo) ve seslendirme metni tasarımı
  ├──> Step 5: Duo Script Generation ──> İki sesli diyalog oluşturma ve şema kontrolü
  ├──> Step 6: Caption & Hashtag ──> Instagram Reels açıklaması ve etiket üretimi
  ├──> Step 7: Threads Metni ──> Soru içermeyen ilgi çekici Threads içeriği üretimi
  ├──> Step 8: Quality Assurance (QA) ──> LLM tabanlı son kontrol (Hatalıysa Step 4 veya 5'e dönerek yeniden dener)
  ├──> Step 9: TTS Ses Üretimi ──> (Gemini Multi-Speaker TTS) ──> Autonoe + Charon diyalog ses dosyası (WAV)
  │
[FFMPEG BİRLEŞTİRME] (core/media.py)
  │
  ├──> Ses 1.20x hızlandırılır
  ├──> Video, ses süresine göre tam senkron hızlandırılır/yavaşlatılır
  └──> Final MP4 çıktısı hazırlanır
         │
[TELEGRAM İLETİMİ] (telegram_pipeline_worker.py)
  │
  ├──> Final Video kanala yüklenir
  ├──> Başlık Seçenekleri ayrı mesaj olarak yollanır
  └──> Instagram Caption ve Threads postu gönderilir
```

---

## 🛡️ 4. Hata Toleransı ve Güvenlik Ağları (Resilience & Guards)

Sistem, harici API'lere (Gemini, Telegram) ve ağ koşullarına bağımlı olduğu için çok katmanlı koruma mekanizmalarıyla donatılmıştır:

1.  **API Key Rotasyonu (SmartRouter)**: Herhangi bir Gemini API anahtarı `Rate Limit (429)` veya `Quota Exhausted` hatası aldığında, `SmartRouter` o anahtarı geçici süreyle kara listeye alır ve sıradaki anahtarla isteği baştan dener. Hiçbir istek yarıda kalmaz.
2.  **Lokal Bölme Koruması (Duo Guard)**: Canlı ortamda Gemini TTS veya LLM diyalog motoru geçici bir hata verdiğinde, sistem tek geçişli korumayı tetikler. Onaylanmış Reels metnini lokal olarak cümle bazında bölerek diyalog segmentlerine atar ve üretimi kesintisiz tamamlar.
3.  **Fact-Lock Tabanlı Sosyal Fallback**: LLM'ler caption veya threads üretirken boş dönerse veya içinde dosya yolu `/tmp/...` gibi yapay zeka kalıntıları (artifact) barındırırsa, sosyal koruyucu bunu yakalar. Yerel kural tabanlı motor devreye girerek Fact Lock içindeki doğrulanmış gerçeklerden tertemiz ve profesyonel bir caption & threads metni oluşturur.
4.  **Süre Senkronizasyon Limitleri**: Ses ve video süresi uyuşmadığında video hızı en fazla `1.50x` hızlandırılır veya en az `0.50x` yavaşlatılır. Bu sınırlar dışına çıkılmayarak videonun aşırı hızlanıp izlenemez hale gelmesi engellenir.
5.  **Duo Script QA Muafiyeti**: Eğer QA aşaması sadece Duo Script katmanında hata bulursa fakat halihazırda üretilmiş ve doğrulanmış bir TTS ses dosyası sistemde mevcutsa, pipeline akışı kesilmez, mevcut ses korunarak montaj tamamlanır.
