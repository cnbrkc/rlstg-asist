"""
Ultimate Content Engine — Pipeline Şemaları

Her editoryal rolün kendi şeması var. Şemalar promptların istediği alanları korur;
özellikle Reels Creative'de ana + alt kapak başlıkları ve Editorial'de Threads'in
kullandığı discussion_territory alanı kaybolmaz.
"""

VIDEO_ANALYSIS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "video_identity": {"type": "OBJECT", "properties": {"brand": {"type": "STRING"}, "exact_model": {"type": "STRING", "description": "Tam model adı. Emin değilsen 'UNKNOWN' yaz. Benzer modelin adını uydurma."}, "variant": {"type": "STRING"}, "generation": {"type": "STRING"}, "confidence": {"type": "STRING", "description": "high / medium / low / unknown"}}, "required": ["brand", "exact_model", "confidence"]},
        "kapak_ani_saniye": {"type": "NUMBER", "description": "Kapak/hook için en çarpıcı anın videodaki saniyesi."},
        "timeline": {"type": "ARRAY", "description": "Videoyu görsel geçişlere göre bölen zaman çizelgesi.", "items": {"type": "OBJECT", "properties": {"baslangic": {"type": "STRING"}, "bitis": {"type": "STRING"}, "olay": {"type": "STRING"}, "arac_hareketi": {"type": "STRING"}, "kamera_hareketi": {"type": "STRING"}, "ekran_yazisi": {"type": "STRING"}, "teknik_gorsel_detay": {"type": "STRING"}}, "required": ["olay"]}},
        "observed_facts": {"type": "ARRAY", "items": {"type": "STRING"}}, "unknowns": {"type": "ARRAY", "items": {"type": "STRING"}}, "possible_inference": {"type": "ARRAY", "items": {"type": "STRING"}}, "visual_opportunities": {"type": "ARRAY", "items": {"type": "STRING"}}, "viral_arastirma_ihtiyaclari": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["video_identity", "kapak_ani_saniye", "timeline", "observed_facts", "unknowns", "possible_inference", "visual_opportunities"],
}

FACT_LOCK_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "facts": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"fact": {"type": "STRING"}, "status": {"type": "STRING", "description": "OBSERVED / VERIFIED / INFERENCE / UNKNOWN"}, "source": {"type": "STRING"}, "source_type": {"type": "STRING"}, "confidence": {"type": "STRING"}}, "required": ["fact", "status"]}},
        "turkiye_satis_durumu": {"type": "STRING", "description": "VAR / YOK / BILINMIYOR"},
        "turkiye_fiyati": {"type": "STRING"},
        "global_fiyat_bilgisi": {"type": "STRING"},
        "turkiye_ilgi_sinyalleri": {
            "type": "ARRAY",
            "description": "Türkiye kitlesi için doğrulanmış, içerik değeri taşıyan aday açılar; yaratıcı hook metni değildir.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "kategori": {"type": "STRING", "description": "fiyat_deger / kullanim_maliyeti / vergi / teknoloji / performans / pratiklik / tasarim / diger"},
                    "bulgu": {"type": "STRING", "description": "OBSERVED veya VERIFIED dayanağın kısa özeti."},
                    "neden_turkiyede_ilginc": {"type": "STRING"},
                    "guvenli_anlatim": {"type": "STRING", "description": "Belirsizliği koruyan, doğrudan içerikte kullanılabilecek olgusal çerçeve."},
                    "onem_puani": {"type": "NUMBER", "description": "0-10; ekonomik/pratik önem, şaşırtıcılık ve kanıt gücü birlikte."}
                },
                "required": ["kategori", "bulgu", "neden_turkiyede_ilginc", "guvenli_anlatim", "onem_puani"]
            }
        },
        "arastirma_notu": {"type": "STRING"}
    },
    "required": ["facts", "turkiye_satis_durumu", "turkiye_ilgi_sinyalleri"],
}

EDITORIAL_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "story_options": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "isim": {"type": "STRING"},
                    "kategori": {"type": "STRING"},
                    "fact_strength": {"type": "NUMBER"},
                    "turkish_audience_relevance": {"type": "NUMBER"},
                    "economic_or_practical_impact": {"type": "NUMBER"},
                    "surprise_gap": {"type": "NUMBER"},
                    "visual_support": {"type": "NUMBER"},
                    "shareability": {"type": "NUMBER"},
                    "repetition_risk": {"type": "NUMBER"},
                    "toplam_oncelik": {"type": "NUMBER"},
                    "dayanak": {"type": "STRING"}
                },
                "required": ["isim", "kategori", "fact_strength", "turkish_audience_relevance", "economic_or_practical_impact", "surprise_gap", "visual_support", "shareability", "repetition_risk", "toplam_oncelik", "dayanak"]
            }
        },
        "core_story": {"type": "STRING"},
        "selected_story_index": {"type": "INTEGER", "description": "story_options içindeki seçilen adayın 0 tabanlı index'i."},
        "selected_story_category": {"type": "STRING"},
        "selection_rationale": {"type": "STRING", "description": "En yüksek öncelikli açı seçildiyse neden; seçilmediyse kanıta dayalı istisna gerekçesi."},
        "why_it_matters": {"type": "STRING"},
        "primary_facts": {"type": "ARRAY", "items": {"type": "STRING"}},
        "visual_moments": {"type": "ARRAY", "items": {"type": "STRING"}},
        "audience_trigger": {"type": "STRING"},
        "tone": {"type": "STRING"},
        "things_to_avoid": {"type": "ARRAY", "items": {"type": "STRING"}},
        "potential_hook_territories": {"type": "ARRAY", "items": {"type": "STRING"}},
        "discussion_territory": {"type": "STRING", "description": "Threads için tartışma potansiyeli olan açı."}
    },
    "required": ["story_options", "core_story", "selected_story_index", "selected_story_category", "selection_rationale", "why_it_matters", "primary_facts", "audience_trigger", "tone"],
}

REELS_CREATIVE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "beyin_firtinasi": {"type": "STRING"}, "veri_kilitleme": {"type": "STRING"}, "oz_elestiri": {"type": "STRING"},
        "turkiye_ilgi_kancasi": {"type": "STRING", "description": "Editorial Brief'ten taşınan en güçlü doğrulanmış Türkiye ilgi nedeni."},
        "ana_hikaye_sadakat_kontrolu": {"type": "STRING", "description": "Seslendirme ve hook'un en güçlü editoryal açıyı neden taşıdığının kısa kontrolü."},
        "anlatim_modu": {"type": "STRING", "enum": ["DUO", "SOLO_FEMALE", "SOLO_MALE"], "description": "Kullanıcı açık mod söylediyse onu uygula; söylemediyse video ve hikâyeye göre en doğal anlatım modunu editoryal olarak seç."},
        "duo_stratejisi": {"type": "OBJECT", "properties": {"uygunluk": {"type": "STRING", "enum": ["DUO", "SOLO_FEMALE", "SOLO_MALE"], "description": "anlatim_modu ile aynı karar; kullanıcı override'ı yoksa içerik uygunluğuna göre seçilir"}, "hook_speaker": {"type": "STRING", "description": "female / male / none"}, "female_agirligi": {"type": "NUMBER", "description": "0-1 arasında yaklaşık yaratıcı ağırlık; matematiksel zorunluluk değildir."}, "male_agirligi": {"type": "NUMBER", "description": "0-1 arasında yaklaşık yaratıcı ağırlık; matematiksel zorunluluk değildir."}, "interaction_level": {"type": "NUMBER", "description": "0-1. Karakterlerin birbirine doğrudan tepki verme düzeyi."}, "humor_level": {"type": "NUMBER", "description": "0-1. İçeriğe uygun mizah düzeyi."}, "tension_level": {"type": "NUMBER", "description": "0-1. Hafif fikir ayrılığı/çekişme düzeyi; marka hedefleme değildir."}, "selected_detail": {"type": "STRING", "description": "Diyaloğun merkezine alınabilecek en güçlü vurucu detay."}, "ending_speaker": {"type": "STRING", "description": "female / male / none"}, "rationale": {"type": "STRING"}}, "required": ["uygunluk", "hook_speaker", "female_agirligi", "male_agirligi", "interaction_level", "humor_level", "tension_level", "selected_detail", "ending_speaker", "rationale"]},
        "konusma_haritasi": {"type": "ARRAY", "description": "Henüz tam cümle yazmadan, konuşmanın ritmini ve görevlerini planlayan segment haritası.", "items": {"type": "OBJECT", "properties": {"sira": {"type": "INTEGER"}, "speaker": {"type": "STRING", "description": "female / male"}, "amac": {"type": "STRING"}, "detay": {"type": "STRING"}, "duygu": {"type": "STRING"}}, "required": ["sira", "speaker", "amac", "detay"]}},
        "hook_families": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"kapak_ana": {"type": "STRING"}, "kapak_alt": {"type": "STRING"}, "ilk_uc_saniye": {"type": "STRING"}, "anlati_yonu": {"type": "STRING"}, "curiosity_score": {"type": "NUMBER"}, "visual_match_score": {"type": "NUMBER"}, "fact_strength_score": {"type": "NUMBER"}, "originality_score": {"type": "NUMBER"}, "retention_score": {"type": "NUMBER"}}, "required": ["kapak_ana", "kapak_alt", "ilk_uc_saniye", "anlati_yonu"]}},
        "secilen_aile_index": {"type": "INTEGER"},
        "kapak_basliklari": {"type": "ARRAY", "description": "5 alternatif ana + alt kapak seti.", "items": {"type": "OBJECT", "properties": {"ana": {"type": "STRING"}, "alt": {"type": "STRING"}}, "required": ["ana", "alt"]}},
        "seslendirme_metni": {"type": "STRING"},
    },
    "required": ["turkiye_ilgi_kancasi", "ana_hikaye_sadakat_kontrolu", "hook_families", "secilen_aile_index", "kapak_basliklari", "seslendirme_metni", "anlatim_modu", "duo_stratejisi", "konusma_haritasi"],
}

DUO_SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "segments": {"type": "ARRAY", "description": "Sıralı konuşma blokları. Yalnızca sözleşmede izin verilen speaker kullanılabilir.", "items": {"type": "OBJECT", "properties": {"speaker": {"type": "STRING", "description": "female veya male"}, "text": {"type": "STRING"}}, "required": ["speaker", "text"]}},
    },
    "required": ["segments"],
}

CAPTION_SCHEMA = {"type": "OBJECT", "properties": {"reels_aciklamasi": {"type": "STRING"}, "reels_hashtagleri": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["reels_aciklamasi", "reels_hashtagleri"]}
THREADS_SCHEMA = {"type": "OBJECT", "properties": {"threads_aciklamasi": {"type": "STRING", "description": "Max 500 karakter. Soru cümlesi ve hashtag yok."}}, "required": ["threads_aciklamasi"]}
QA_SCHEMA = {
    "type": "OBJECT", "properties": {"fact_check": {"type": "STRING"}, "model_check": {"type": "STRING"}, "video_check": {"type": "STRING"}, "current_data_check": {"type": "STRING"}, "cover_check": {"type": "STRING"}, "hook_check": {"type": "STRING"}, "visual_match_check": {"type": "STRING"}, "repetition_check": {"type": "STRING"}, "tts_check": {"type": "STRING"}, "length_check": {"type": "STRING"}, "caption_check": {"type": "STRING"}, "hashtag_check": {"type": "STRING"}, "threads_check": {"type": "STRING"}, "brand_check": {"type": "STRING"}, "tone_check": {"type": "STRING"}, "viral_priority_check": {"type": "STRING"}, "duo_check": {"type": "STRING"}, "overall": {"type": "STRING"}, "regeneration_targets": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["tone_check", "viral_priority_check", "overall", "regeneration_targets"],
}
