"""
Ultimate Content Engine — Pipeline Şemaları

Her editoryal rolün kendi şeması var. Şemalar promptların istediği alanları korur;
özellikle Reels Creative'de ana + alt kapak başlıkları ve Editorial'de Threads'in
kullandığı discussion_territory alanı kaybolmaz.
"""

VIDEO_ANALYSIS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "video_identity": {
            "type": "OBJECT",
            "properties": {
                "brand": {"type": "STRING"},
                "exact_model": {"type": "STRING", "description": "Tam model adı. Emin değilsen 'UNKNOWN' yaz. Benzer modelin adını uydurma."},
                "variant": {"type": "STRING"},
                "generation": {"type": "STRING"},
                "confidence": {"type": "STRING", "description": "high / medium / low / unknown"},
            },
            "required": ["brand", "exact_model", "confidence"],
        },
        "kapak_ani_saniye": {"type": "NUMBER", "description": "Kapak/hook için en çarpıcı anın videodaki saniyesi."},
        "timeline": {
            "type": "ARRAY",
            "description": "Videoyu görsel geçişlere göre bölen zaman çizelgesi.",
            "items": {"type": "OBJECT", "properties": {
                "baslangic": {"type": "STRING"}, "bitis": {"type": "STRING"}, "olay": {"type": "STRING"},
                "arac_hareketi": {"type": "STRING"}, "kamera_hareketi": {"type": "STRING"},
                "ekran_yazisi": {"type": "STRING"}, "teknik_gorsel_detay": {"type": "STRING"},
            }, "required": ["olay"]},
        },
        "observed_facts": {"type": "ARRAY", "items": {"type": "STRING"}},
        "unknowns": {"type": "ARRAY", "items": {"type": "STRING"}},
        "possible_inference": {"type": "ARRAY", "items": {"type": "STRING"}},
        "visual_opportunities": {"type": "ARRAY", "items": {"type": "STRING"}},
        "viral_arastirma_ihtiyaclari": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["video_identity", "kapak_ani_saniye", "timeline", "observed_facts", "unknowns", "possible_inference", "visual_opportunities"],
}

FACT_LOCK_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "facts": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "fact": {"type": "STRING"}, "status": {"type": "STRING", "description": "OBSERVED / VERIFIED / INFERENCE / UNKNOWN"},
            "source": {"type": "STRING"}, "source_type": {"type": "STRING"}, "confidence": {"type": "STRING"},
        }, "required": ["fact", "status"]}},
        "turkiye_satis_durumu": {"type": "STRING", "description": "VAR / YOK / BILINMIYOR"},
        "turkiye_fiyati": {"type": "STRING"},
        "global_fiyat_bilgisi": {"type": "STRING"},
        "arastirma_notu": {"type": "STRING"},
    },
    "required": ["facts", "turkiye_satis_durumu"],
}

EDITORIAL_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "story_options": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "isim": {"type": "STRING"}, "visual_relevance": {"type": "STRING"}, "fact_strength": {"type": "STRING"},
            "curiosity": {"type": "STRING"}, "novelty": {"type": "STRING"}, "emotional_trigger": {"type": "STRING"},
            "turkish_audience_relevance": {"type": "STRING"}, "shareability": {"type": "STRING"}, "repetition_risk": {"type": "STRING"},
        }, "required": ["isim"]}},
        "core_story": {"type": "STRING"}, "why_it_matters": {"type": "STRING"},
        "primary_facts": {"type": "ARRAY", "items": {"type": "STRING"}},
        "visual_moments": {"type": "ARRAY", "items": {"type": "STRING"}},
        "audience_trigger": {"type": "STRING"}, "tone": {"type": "STRING"},
        "things_to_avoid": {"type": "ARRAY", "items": {"type": "STRING"}},
        "potential_hook_territories": {"type": "ARRAY", "items": {"type": "STRING"}},
        "discussion_territory": {"type": "STRING", "description": "Threads için tartışma potansiyeli olan açı."},
    },
    "required": ["core_story", "why_it_matters", "primary_facts", "audience_trigger", "tone"],
}

REELS_CREATIVE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "beyin_firtinasi": {"type": "STRING"},
        "veri_kilitleme": {"type": "STRING"},
        "oz_elestiri": {"type": "STRING"},
        "anlatim_modu": {"type": "STRING", "description": "SOLO_FEMALE / SOLO_MALE / DUO. İçeriğe göre seç. Varsayılan eğilim DUO'dur; solo yalnızca içerik bunu daha güçlü kılıyorsa seçilir."},
        "duo_stratejisi": {"type": "OBJECT", "properties": {
            "uygunluk": {"type": "STRING", "description": "DUO / SOLO_FEMALE / SOLO_MALE"},
            "hook_speaker": {"type": "STRING", "description": "female / male / none"},
            "female_agirligi": {"type": "NUMBER", "description": "0-1 arasında yaklaşık yaratıcı ağırlık; matematiksel zorunluluk değildir."},
            "male_agirligi": {"type": "NUMBER", "description": "0-1 arasında yaklaşık yaratıcı ağırlık; matematiksel zorunluluk değildir."},
            "interaction_level": {"type": "NUMBER", "description": "0-1. Karakterlerin birbirine doğrudan tepki verme düzeyi."},
            "humor_level": {"type": "NUMBER", "description": "0-1. İçeriğe uygun mizah düzeyi."},
            "tension_level": {"type": "NUMBER", "description": "0-1. Hafif fikir ayrılığı/çekişme düzeyi; marka hedefleme değildir."},
            "selected_detail": {"type": "STRING", "description": "Diyaloğun merkezine alınabilecek en güçlü vurucu detay."},
            "ending_speaker": {"type": "STRING", "description": "female / male / none"},
            "rationale": {"type": "STRING"},
        }, "required": ["uygunluk", "hook_speaker", "female_agirligi", "male_agirligi", "interaction_level", "humor_level", "tension_level", "selected_detail", "ending_speaker", "rationale"]},
        "konusma_haritasi": {"type": "ARRAY", "description": "Henüz tam cümle yazmadan, konuşmanın ritmini ve görevlerini planlayan segment haritası. Solo modda tek konuşmacı kullanılabilir. Duo modda yalnızca gerçekten değer katan dönüşler eklenir.", "items": {"type": "OBJECT", "properties": {
            "sira": {"type": "INTEGER"},
            "speaker": {"type": "STRING", "description": "female / male"},
            "amac": {"type": "STRING", "description": "hook / fact / reaction / challenge / explanation / counterpoint / transition / punchline / closing"},
            "detay": {"type": "STRING"},
            "duygu": {"type": "STRING"}
        }, "required": ["sira", "speaker", "amac", "detay"]}},
        "hook_families": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "kapak_ana": {"type": "STRING"}, "kapak_alt": {"type": "STRING"},
            "ilk_uc_saniye": {"type": "STRING"}, "anlati_yonu": {"type": "STRING"},
            "curiosity_score": {"type": "NUMBER"}, "visual_match_score": {"type": "NUMBER"},
            "fact_strength_score": {"type": "NUMBER"}, "originality_score": {"type": "NUMBER"}, "retention_score": {"type": "NUMBER"},
        }, "required": ["kapak_ana", "kapak_alt", "ilk_uc_saniye", "anlati_yonu"]}},
        "secilen_aile_index": {"type": "INTEGER"},
        "kapak_basliklari": {"type": "ARRAY", "description": "5 alternatif ana + alt kapak seti.", "items": {
            "type": "OBJECT", "properties": {"ana": {"type": "STRING"}, "alt": {"type": "STRING"}}, "required": ["ana", "alt"]
        }},
        "seslendirme_metni": {"type": "STRING"},
    },
    "required": ["hook_families", "secilen_aile_index", "kapak_basliklari", "seslendirme_metni", "anlatim_modu", "duo_stratejisi", "konusma_haritasi"],
}

CAPTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "reels_aciklamasi": {"type": "STRING"},
        "reels_hashtagleri": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["reels_aciklamasi", "reels_hashtagleri"],
}

THREADS_SCHEMA = {
    "type": "OBJECT",
    "properties": {"threads_aciklamasi": {"type": "STRING", "description": "Max 500 karakter. Soru cümlesi ve hashtag yok."}},
    "required": ["threads_aciklamasi"],
}

QA_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "fact_check": {"type": "STRING"}, "model_check": {"type": "STRING"}, "video_check": {"type": "STRING"},
        "current_data_check": {"type": "STRING"}, "cover_check": {"type": "STRING"}, "hook_check": {"type": "STRING"},
        "visual_match_check": {"type": "STRING"}, "repetition_check": {"type": "STRING"}, "tts_check": {"type": "STRING"},
        "length_check": {"type": "STRING"}, "caption_check": {"type": "STRING"}, "hashtag_check": {"type": "STRING"},
        "threads_check": {"type": "STRING"}, "brand_check": {"type": "STRING"},
        "overall": {"type": "STRING"},
        "regeneration_targets": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["overall", "regeneration_targets"],
}
