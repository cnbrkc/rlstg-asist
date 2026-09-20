VIDEO_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "video_identity": {"type": "object", "properties": {"brand": {"type": "string"}, "exact_model": {"type": "string", "description": "Tam model adı. Emin değilsen 'UNKNOWN' yaz. Benzer modelin adını uydurma."}, "variant": {"type": "string"}, "generation": {"type": "string"}, "confidence": {"type": "string", "description": "high / medium / low / unknown"}}, "required": ["brand", "exact_model", "confidence"]},
        "kapak_ani_saniye": {"type": "number", "description": "Kapak/hook için en çarpıcı anın videodaki saniyesi."},
        "timeline": {"type": "array", "description": "Videoyu görsel geçişlere göre bölen zaman çizelgesi.", "items": {"type": "object", "properties": {"baslangic": {"type": "string"}, "bitis": {"type": "string"}, "olay": {"type": "string"}, "arac_hareketi": {"type": "string"}, "kamera_hareketi": {"type": "string"}, "ekran_yazisi": {"type": "string"}, "teknik_gorsel_detay": {"type": "string"}}, "required": ["olay"]}},
        "observed_facts": {"type": "array", "items": {"type": "string"}}, "unknowns": {"type": "array", "items": {"type": "string"}}, "possible_inference": {"type": "array", "items": {"type": "string"}}, "visual_opportunities": {"type": "array", "items": {"type": "string"}}, "viral_arastirma_ihtiyaclari": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["video_identity", "kapak_ani_saniye", "timeline", "observed_facts", "unknowns", "possible_inference", "visual_opportunities"],
}

FACT_LOCK_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {"type": "object", "properties": {"fact": {"type": "string"}, "status": {"type": "string", "description": "OBSERVED / VERIFIED / INFERENCE / UNKNOWN"}, "source": {"type": "string"}, "source_type": {"type": "string"}, "confidence": {"type": "string"}}, "required": ["fact", "status"]}},
        "turkiye_satis_durumu": {"type": "string", "description": "VAR / YOK / BILINMIYOR"},
        "turkiye_fiyati": {"type": "string"},
        "global_fiyat_bilgisi": {"type": "string"},
        "turkiye_ilgi_sinyalleri": {
            "type": "array",
            "description": "Türkiye kitlesi için doğrulanmış, içerik değeri taşıyan aday açılar; yaratıcı hook metni değildir.",
            "items": {
                "type": "object",
                "properties": {
                    "kategori": {"type": "string", "description": "fiyat_deger / kullanim_maliyeti / vergi / teknoloji / performans / pratiklik / tasarim / diger"},
                    "bulgu": {"type": "string", "description": "OBSERVED veya VERIFIED dayanağın kısa özeti."},
                    "neden_turkiyede_ilginc": {"type": "string"},
                    "guvenli_anlatim": {"type": "string", "description": "Belirsizliği koruyan, doğrudan içerikte kullanılabilecek olgusal çerçeve."},
                    "onem_puani": {"type": "number", "description": "0-10; ekonomik/pratik önem, şaşırtıcılık ve kanıt gücü birlikte."}
                },
                "required": ["kategori", "bulgu", "neden_turkiyede_ilginc", "guvenli_anlatim", "onem_puani"]
            }
        },
        "arastirma_notu": {"type": "string"}
    },
    "required": ["facts", "turkiye_satis_durumu", "turkiye_ilgi_sinyalleri"],
}

EDITORIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "story_options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "isim": {"type": "string"},
                    "kategori": {"type": "string"},
                    "fact_strength": {"type": "number"},
                    "turkish_audience_relevance": {"type": "number"},
                    "economic_or_practical_impact": {"type": "number"},
                    "surprise_gap": {"type": "number"},
                    "visual_support": {"type": "number"},
                    "shareability": {"type": "number"},
                    "repetition_risk": {"type": "number"},
                    "toplam_oncelik": {"type": "number"},
                    "dayanak": {"type": "string"}
                },
                "required": ["isim", "kategori", "fact_strength", "turkish_audience_relevance", "economic_or_practical_impact", "surprise_gap", "visual_support", "shareability", "repetition_risk", "toplam_oncelik", "dayanak"]
            }
        },
        "core_story": {"type": "string"},
        "selected_story_index": {"type": "integer", "description": "story_options içindeki seçilen adayın 0 tabanlı index'i."},
        "selected_story_category": {"type": "string"},
        "selection_rationale": {"type": "string", "description": "En yüksek öncelikli açı seçildiyse neden; seçilmediyse kanıta dayalı istisna gerekçesi."},
        "why_it_matters": {"type": "string"},
        "primary_facts": {"type": "array", "items": {"type": "string"}},
        "visual_moments": {"type": "array", "items": {"type": "string"}},
        "audience_trigger": {"type": "string"},
        "tone": {"type": "string"},
        "things_to_avoid": {"type": "array", "items": {"type": "string"}},
        "potential_hook_territories": {"type": "array", "items": {"type": "string"}},
        "discussion_territory": {"type": "string", "description": "Threads için tartışma potansiyeli olan açı."}
    },
    "required": ["story_options", "core_story", "selected_story_index", "selected_story_category", "selection_rationale", "why_it_matters", "primary_facts", "audience_trigger", "tone"],
}

REELS_CREATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "beyin_firtinasi": {"type": "string"}, "veri_kilitleme": {"type": "string"}, "oz_elestiri": {"type": "string"},
        "turkiye_ilgi_kancasi": {"type": "string", "description": "Editorial Brief'ten taşınan en güçlü doğrulanmış Türkiye ilgi nedeni."},
        "ana_hikaye_sadakat_kontrolu": {"type": "string", "description": "Seslendirme ve hook'un en güçlü editoryal açıyı neden taşıdığının kısa kontrolü."},
        "anlatim_modu": {"type": "string", "enum": ["DUO", "SOLO_FEMALE", "SOLO_MALE"], "description": "Kullanıcı açık mod söylediyse onu uygula; söylemediyse video ve hikâyeye göre en doğal anlatım modunu editoryal olarak seç."},
        "duo_stratejisi": {"type": "object", "properties": {"uygunluk": {"type": "string", "enum": ["DUO", "SOLO_FEMALE", "SOLO_MALE"], "description": "anlatim_modu ile aynı karar; kullanıcı override'ı yoksa içerik uygunluğuna göre seçilir"}, "hook_speaker": {"type": "string", "description": "female / male / none"}, "female_agirligi": {"type": "number", "description": "0-1 arasında yaklaşık yaratıcı ağırlık; matematiksel zorunluluk değildir."}, "male_agirligi": {"type": "number", "description": "0-1 arasında yaklaşık yaratıcı ağırlık; matematiksel zorunluluk değildir."}, "interaction_level": {"type": "number", "description": "0-1. Karakterlerin birbirine doğrudan tepki verme düzeyi."}, "humor_level": {"type": "number", "description": "0-1. İçeriğe uygun mizah düzeyi."}, "tension_level": {"type": "number", "description": "0-1. Hafif fikir ayrılığı/çekişme düzeyi; marka hedefleme değildir."}, "selected_detail": {"type": "string", "description": "Diyaloğun merkezine alınabilecek en güçlü vurucu detay."}, "ending_speaker": {"type": "string", "description": "female / male / none"}, "rationale": {"type": "string"}}, "required": ["uygunluk", "hook_speaker", "female_agirligi", "male_agirligi", "interaction_level", "humor_level", "tension_level", "selected_detail", "ending_speaker", "rationale"]},
        "konusma_haritasi": {"type": "array", "description": "Henüz tam cümle yazmadan, konuşmanın ritmini ve görevlerini planlayan segment haritası.", "items": {"type": "object", "properties": {"sira": {"type": "integer"}, "speaker": {"type": "string", "description": "female / male"}, "amac": {"type": "string"}, "detay": {"type": "string"}, "duygu": {"type": "string"}}, "required": ["sira", "speaker", "amac", "detay"]}},
        "hook_families": {"type": "array", "items": {"type": "object", "properties": {"kapak_ana": {"type": "string"}, "kapak_alt": {"type": "string"}, "ilk_uc_saniye": {"type": "string"}, "anlati_yonu": {"type": "string"}, "curiosity_score": {"type": "number"}, "visual_match_score": {"type": "number"}, "fact_strength_score": {"type": "number"}, "originality_score": {"type": "number"}, "retention_score": {"type": "number"}}, "required": ["kapak_ana", "kapak_alt", "ilk_uc_saniye", "anlati_yonu"]}},
        "secilen_aile_index": {"type": "integer"},
        "kapak_basliklari": {"type": "array", "description": "5 alternatif ana + alt kapak seti.", "items": {"type": "object", "properties": {"ana": {"type": "string"}, "alt": {"type": "string"}}, "required": ["ana", "alt"]}},
        "seslendirme_metni": {"type": "string"},
    },
    "required": ["turkiye_ilgi_kancasi", "ana_hikaye_sadakat_kontrolu", "hook_families", "secilen_aile_index", "kapak_basliklari", "seslendirme_metni", "anlatim_modu", "duo_stratejisi", "konusma_haritasi"],
}

DUO_SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "conversation_design": {
            "type": "object",
            "properties": {
                "central_tension": {"type": "string"},
                "hook_open_loop": {"type": "string"},
                "reversal": {"type": "string"},
                "payoff_callback": {"type": "string"}
            },
            "required": ["central_tension", "hook_open_loop", "reversal", "payoff_callback"]
        },
        "segments": {
            "type": "array",
            "description": "Sıralı konuşma blokları. Yalnızca sözleşmede izin verilen speaker kullanılabilir.",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string", "description": "female veya male"},
                    "purpose": {"type": "string", "description": "hook / rebuttal / fact / counterpoint / concession / backchannel / callback / closing"},
                    "reply_anchor": {"type": "string", "description": "İlk turda OPENING; diğerlerinde önceki replikten yakalanan kısa somut ifade."},
                    "text": {"type": "string"}
                },
                "required": ["speaker", "purpose", "reply_anchor", "text"]
            }
        },
    },
    "required": ["conversation_design", "segments"],
}

CAPTION_SCHEMA = {"type": "object", "properties": {"reels_aciklamasi": {"type": "string"}, "reels_hashtagleri": {"type": "array", "items": {"type": "string"}}}, "required": ["reels_aciklamasi", "reels_hashtagleri"]}
THREADS_SCHEMA = {"type": "object", "properties": {"threads_aciklamasi": {"type": "string", "description": "Max 500 karakter. Soru cümlesi ve hashtag yok."}}, "required": ["threads_aciklamasi"]}
QA_SCHEMA = {
    "type": "object",
    "properties": {"fact_check": {"type": "string"}, "model_check": {"type": "string"}, "video_check": {"type": "string"}, "current_data_check": {"type": "string"}, "cover_check": {"type": "string"}, "hook_check": {"type": "string"}, "visual_match_check": {"type": "string"}, "repetition_check": {"type": "string"}, "tts_check": {"type": "string"}, "length_check": {"type": "string"}, "caption_check": {"type": "string"}, "hashtag_check": {"type": "string"}, "threads_check": {"type": "string"}, "brand_check": {"type": "string"}, "tone_check": {"type": "string"}, "viral_priority_check": {"type": "string"}, "duo_check": {"type": "string"}, "overall": {"type": "string"}, "regeneration_targets": {"type": "array", "items": {"type": "string"}}},
    "required": ["tone_check", "viral_priority_check", "overall", "regeneration_targets"],
}
