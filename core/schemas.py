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
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "status": {"type": "string", "description": "OBSERVED / VERIFIED / INFERENCE / UNKNOWN / CONTRADICTED"},
                    "source": {"type": "string"},
                    "source_type": {"type": "string", "description": "official / manufacturer / industry / news / forum / other"},
                    "confidence": {"type": "string"}
                },
                "required": ["fact", "status"]
            }
        },
        "turkiye_satis_durumu": {"type": "string", "description": "VAR / YOK / BILINMIYOR"},
        "turkiye_fiyati": {"type": "string"},
        "global_fiyat_bilgisi": {"type": "string"},
        "turkiye_ilgi_sinyalleri": {
            "type": "array",
            "description": "Türkiye kitlesi için doğrulanmış, içerik değeri taşıyan aday açılar; yaratıcı hook metni değildir.",
            "items": {
                "type": "object",
                "properties": {
                    "kategori": {"type": "string", "description": "celiski / fiyat_deger / kullanim_maliyeti / vergi / teknoloji / performans / pratiklik / tasarim / diger"},
                    "bulgu": {"type": "string", "description": "OBSERVED, VERIFIED veya CONTRADICTED dayanağın kısa özeti. Çelişki varsa iki tarafı da yaz."},
                    "neden_turkiyede_ilginc": {"type": "string", "description": "Türkiye kitlesindeki somut karşılığı ve tartışma potansiyeli."},
                    "guvenli_anlatim": {"type": "string", "description": "Belirsizliği koruyan, doğrudan içerikte kullanılabilecek olgusal çerçeve."},
                    "onem_puani": {"type": "number", "description": "0-10; ekonomik/pratik önem, şaşırtıcılık, kanıt gücü ve tartışma potansiyeli birlikte."}
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

CAPTION_SCHEMA = {"type": "object", "properties": {"reels_aciklamasi": {"type": "string"}, "reels_hashtagleri": {"type": "array", "items": {"type": "string"}}}, "required": ["reels_aciklamasi", "reels_hashtagleri"]}
THREADS_SCHEMA = {"type": "object", "properties": {"threads_aciklamasi": {"type": "string", "description": "Max 500 karakter. Soru cümlesi ve hashtag yok."}}, "required": ["threads_aciklamasi"]}
QA_SCHEMA = {
    "type": "object",
    "properties": {"fact_check": {"type": "string"}, "model_check": {"type": "string"}, "video_check": {"type": "string"}, "current_data_check": {"type": "string"}, "cover_check": {"type": "string"}, "hook_check": {"type": "string"}, "visual_match_check": {"type": "string"}, "repetition_check": {"type": "string"}, "tts_check": {"type": "string"}, "length_check": {"type": "string"}, "caption_check": {"type": "string"}, "hashtag_check": {"type": "string"}, "threads_check": {"type": "string"}, "brand_check": {"type": "string"}, "tone_check": {"type": "string"}, "viral_priority_check": {"type": "string"}, "duo_check": {"type": "string"}, "overall": {"type": "string"}, "regeneration_targets": {"type": "array", "items": {"type": "string"}}},
    "required": ["tone_check", "viral_priority_check", "overall", "regeneration_targets"],
}

# ==========================================
# AGENTİK SİSTEM İÇİN YENİ ŞEMALAR (4 AJAN)
# ==========================================

DETECTIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "kronik_sikayetler": {"type": "array", "items": {"type": "string"}, "description": "Forumlardaki en absürt ve sinir bozucu kronik şikayetler."},
        "turkiye_ozel_magduriyet": {"type": "string", "description": "Vergi, ÖTV veya yol şartlarına dair Türk izleyicisini tetikleyen veri."},
        "viral_kan_mali": {"type": "string", "description": "Dedektifin bulduğu en kışkırtıcı, tek cümlelik ham bilgi."}
    },
    "required": ["kronik_sikayetler", "turkiye_ozel_magduriyet", "viral_kan_mali"]
}

HOOK_GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "secilen_sablon": {"type": "string", "enum": ["Efsane_Curutme", "Ters_Kose", "Negatif_Uyari"]},
        "kapak_basliklari": {
            "type": "array",
            "description": (
                "[Kural: Reels Kapak Yazısı Formatı] TAM 5 FARKLI kapak alternatifi; tek başlık ASLA yeterli değildir. "
                "Her alternatif iki katman: ust = dikkat çekici kanca, TAMAMI BÜYÜK HARF, 2-4 kelime (kaydırmayı durdurur, merak uyandırır); "
                "alt = tamamlayıcı detay, cümle düzeni (yalnızca ilk harf büyük), 4-7 kelime (merakı açıklar, izlemek için sebep verir)."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "ust": {"type": "string", "description": "ÜST BAŞLIK: 2-4 kelime, TAMAMI BÜYÜK HARF."},
                    "alt": {"type": "string", "description": "Alt başlık: 4-7 kelime, yalnızca ilk harf büyük."}
                },
                "required": ["ust", "alt"]
            }
        },
        "kapak_metni": {"type": "string", "description": "En güçlü alternatifin ÜST başlığı (2-4 kelime, TAMAMI BÜYÜK HARF). kapak_basliklari[0].ust ile aynı."},
        "ilk_3_saniye_kanca": {"type": "string", "description": "Seslendirmenin ilk cümlesi. İzleyiciyi şok etmeli; kapak başlığını birebir tekrar etmemeli."}
    },
    "required": ["secilen_sablon", "kapak_basliklari", "kapak_metni", "ilk_3_saniye_kanca"]
}

SCRIPT_WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string", "enum": ["female", "male"]},
                    "tts_tag": {"type": "string", "description": "Yalnızca bu alan. Konuşulan text'e yazma. Örn: [vurgulu], [alaycı], [savunarak], [şaşırarak]. TTS bu kelimeleri okumaz."},
                    "text": {"type": "string"}
                },
                "required": ["speaker", "tts_tag", "text"]
            }
        },
        "yorum_tetikleyici_soru": {"type": "string", "description": "Senaryoyu bitiren, izleyiciyi ikiye bölen o son kışkırtıcı soru."}
    },
    "required": ["segments", "yorum_tetikleyici_soru"]
}

CRITIC_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 1, "maximum": 10},
        "approved": {"type": "boolean"},
        "feedback": {"type": "string", "description": "Onaylanmadıysa yazar ajana verilecek tek ve net revize talimatı."}
    },
    "required": ["score", "approved", "feedback"]
}

METADATA_GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "reels_baslik": {"type": "string"},
        "reels_aciklama": {"type": "string"},
        "reels_hashtag": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["reels_baslik", "reels_aciklama", "reels_hashtag"]
}

