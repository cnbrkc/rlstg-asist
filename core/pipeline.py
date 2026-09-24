"""pipeline.py — Üretim orkestratörü.

Katmanlar:
  1. pipeline_calistir      : video girdili tam üretim (forensic → research →
     editorial → agentic döngü → QA → FFmpeg render → payload).
  2. metin_pipeline_calistir: video analizi olmadan metin girdili üretim.
  3. _qa_regeneration_loop  : agentic üretim + caption/threads + final QA ve
     kontrollü (maks 1) yenileme; QA yalnızca DUO scripti işaretlerken geçerli
     DUO TTS varsa render'ı boğmaz (duo_nonblocking_fallback).

Sosyal korumalar (artifact/boş çıktı doğrulaması + Fact Lock tabanlı fallback)
bu modülde NİTELEMSİZ (native) uygulanır; ayrı monkey-patch katmanı yoktur.
Agentic 4 ajanlı üretim döngüsü core.agentic'tedir.
"""
import json
import os
import re
import shutil

from core.agentic import (
    _beklenen_gercek_mod, _coerce_positive_float, _hizli_basarislik,
    _istek_profili, _run_timed, _yakin_zamanda_asiri_yuk,
    _object_state_or_empty,
    agentic_icerik_uretimi,
)
from core.web_search import web_arastirma_yap
from core.config import PIPELINE_ADIMLARI
from core.schemas import (
    VIDEO_ANALYSIS_SCHEMA, FACT_LOCK_SCHEMA, EDITORIAL_SCHEMA,
    CAPTION_SCHEMA, THREADS_SCHEMA, QA_SCHEMA,
)
from core.prompts import (
    forensic_analiz_promptunu_olustur, research_promptunu_olustur, editorial_promptunu_olustur,
    caption_promptunu_olustur, threads_promptunu_olustur,
    qa_promptunu_olustur, durumu_metne_donustur, girdi_birlestir,
)
from core.media import (
    gecici_dosya_yolu, video_ve_sesi_birlestir,
    medya_raporu, video_suresini_al,
)
from core.narration_mode import anlatim_modu_karar_ver
from core.social_fallbacks import (
    caption_fallback, looks_like_artifact, sanitize_hashtags,
    text as _text, threads_fallback,
)

TOPLAM_ADIM = len(PIPELINE_ADIMLARI)
MAX_QA_REGEN = 1
SOCIAL_REGEN_MAX = 1

QA_REGEN_TARGETS = {
    "VOICEOVER_FAIL",
    "COVER_FAIL",
    "DUO_SCRIPT_FAIL",
    "CAPTION_FAIL",
    "THREADS_FAIL",
}


# --- Küçük yardımcılar ---------------------------------------------------------

def _resolve_video_duration_strict(sure_saniye, temp_input_video):
    direct = _coerce_positive_float(sure_saniye)
    if direct is not None:
        return direct
    if temp_input_video and os.path.exists(temp_input_video):
        measured = _coerce_positive_float(video_suresini_al(temp_input_video))
        if measured is not None:
            return measured
    return None


def _json_object_or_none(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value.strip())
            return parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return None


def _caption_state_normalize(value):
    """Metadata ajanı (`reels_aciklama`/`reels_hashtag`) ve Caption ajanı
    (`reels_aciklamasi`/`reels_hashtagleri`) çıktıları aynı forma indirgenir."""
    parsed = _json_object_or_none(value)
    if parsed is not None:
        aciklama = parsed.get("reels_aciklamasi") or parsed.get("reels_aciklama") or ""
        hashtags = parsed.get("reels_hashtagleri")
        if not isinstance(hashtags, list) or not hashtags:
            hashtags = parsed.get("reels_hashtag")
        return {
            "reels_aciklamasi": str(aciklama or ""),
            "reels_hashtagleri": hashtags if isinstance(hashtags, list) else [],
        }
    if isinstance(value, str) and value.strip():
        return {"reels_aciklamasi": value.strip(), "reels_hashtagleri": []}
    return {"reels_aciklamasi": "", "reels_hashtagleri": []}


def _threads_state_normalize(value):
    parsed = _json_object_or_none(value)
    if parsed is not None:
        return {"threads_aciklamasi": str(parsed.get("threads_aciklamasi", "") or "")}
    if isinstance(value, str) and value.strip():
        return {"threads_aciklamasi": value.strip()}
    return {"threads_aciklamasi": ""}


def _ilerleme(cb, n, msg=None):
    if cb:
        cb(n, TOPLAM_ADIM, msg or PIPELINE_ADIMLARI[n-1])


def _payload(reels_state, caption_state, threads_state, ses_basarili, ses_dosyasi, legacy_voice, model_reels, kullanilan_ses_modeli, model_threads, ses_modu, qa_rounds, final_video, temp_input_video, fact_state, editorial_state, duo_plan, duo_script, qa_state, qa_pass, state, input_media=None, output_media=None, mode='video'):
    reels = _object_state_or_empty(reels_state)
    cap = _caption_state_normalize(caption_state)
    thr = _threads_state_normalize(threads_state)
    payload = {
        'mode': mode,
        'seslendirme_metni': reels.get('seslendirme_metni', ''),
        'reels_aciklamasi': cap.get('reels_aciklamasi', ''),
        'reels_hashtagleri': cap.get('reels_hashtagleri', []),
        'kapak_basliklari': reels.get('kapak_basliklari', []),
        'threads_aciklamasi': thr.get('threads_aciklamasi', ''),
        'ses_basarili': ses_basarili,
        'ses_dosyasi': ses_dosyasi,
        'secilen_ses_ingilizce': legacy_voice,
        'kullanilan_metin_modeli': model_reels,
        'kullanilan_ses_modeli': kullanilan_ses_modeli,
        'kullanilan_threads_modeli': model_threads,
        'ses_modu': ses_modu,
        'ses_modu_sesi': _ses_modu_sesi(ses_modu),
        'qa_regeneration_rounds': qa_rounds,
        'final_video': final_video,
        'temp_input_video': temp_input_video,
        'fact_lock': fact_state,
        'editorial_brief': editorial_state,
        'duo_plan': duo_plan,
        'duo_script': duo_script,
        'qa_result': qa_state,
        'qa_pass': qa_pass,
        'content_tone': (state or {}).get('content_tone', 'dengeli'),
        'pipeline_state': state,
    }
    if input_media is not None:
        payload['input_media'] = input_media
    if output_media is not None:
        payload['output_media'] = output_media
    return payload


def _ses_modu_sesi(mode):
    return {'SOLO_FEMALE': 'Autonoe', 'SOLO_MALE': 'Charon', 'DUO': 'Autonoe + Charon'}.get(mode, mode or 'Bilinmiyor')


# --- Üretim adımları ------------------------------------------------------------

def _forensic_analiz_calistir(router, video_bytes, mime_type, analiz_notlari, sure_saniye, log):
    ek = ''
    if analiz_notlari and analiz_notlari.strip():
        ek = f"\nÖNEMLİ VİDEO ANALİZ NOTLARI:\n{analiz_notlari.strip()}\n"
    return _run_timed(
        log, "Forensic video analizi (Gemini)",
        lambda: router.video_analiz_et(video_bytes, mime_type, forensic_analiz_promptunu_olustur(ek, sure_saniye), VIDEO_ANALYSIS_SCHEMA, log),
    )


def _research_observed_fallback(video_state, exc, log):
    """Dış doğrulama tamamen başarısızsa yeni iddia üretmeden, yalnızca videoda
    gözlenen gerçeklerle güvenli Fact Lock."""
    observed = video_state.get("observed_facts") if isinstance(video_state, dict) else []
    identity = video_state.get("video_identity") if isinstance(video_state, dict) else {}
    facts = []
    if isinstance(identity, dict):
        brand = _text(identity.get("brand"))
        model = _text(identity.get("exact_model"))
        if brand and model:
            facts.append({"fact": f"Videoda tanımlanan araç: {brand} {model}.", "status": "OBSERVED", "source": "Forensic video analysis", "source_type": "video", "confidence": "high"})
        elif model:
            facts.append({"fact": f"Videoda tanımlanan model: {model}.", "status": "OBSERVED", "source": "Forensic video analysis", "source_type": "video", "confidence": "high"})
    for item in observed if isinstance(observed, list) else []:
        t = _text(item)
        if t:
            facts.append({"fact": t, "status": "OBSERVED", "source": "Forensic video analysis", "source_type": "video", "confidence": "high"})
    log(f"⚠️ Research/Search geçici olarak kullanılamadı; yalnızca videoda gözlenen gerçeklerle Fact Lock devam ediyor: {str(exc)[:160]}")
    return {
        "facts": facts,
        "turkiye_satis_durumu": "BILINMIYOR",
        "turkiye_fiyati": "",
        "global_fiyat_bilgisi": "",
        "turkiye_ilgi_sinyalleri": [],
        "arastirma_notu": "Search fallback: dış doğrulama yapılamadı; yeni iddia eklenmedi.",
    }


def _research_calistir(router, video_state, log):
    video_state = _object_state_or_empty(video_state)
    web_sonuclari = web_arastirma_yap(video_state, log)
    content = girdi_birlestir(
        durumu_metne_donustur('VIDEO IDENTITY', video_state.get('video_identity', {})),
        durumu_metne_donustur('OBSERVED FACTS', video_state.get('observed_facts', [])),
        durumu_metne_donustur('UNKNOWNS', video_state.get('unknowns', [])),
        durumu_metne_donustur('POSSIBLE INFERENCE', video_state.get('possible_inference', [])),
        durumu_metne_donustur('ARAŞTIRMA İHTİYAÇLARI', video_state.get('viral_arastirma_ihtiyaclari', [])),
        durumu_metne_donustur('WEB ARAŞTIRMA SONUÇLARI', web_sonuclari or "Web araştırması yapılamadı.")
    )
    try:
        with _istek_profili(router, "uzun_metin"):
            return _run_timed(
                log, "Research / Fact Lock (Agentic Web + Gemini Analiz)",
                lambda: router.metin_uret(content, research_promptunu_olustur(), FACT_LOCK_SCHEMA, log, arama_kullan=False),
            )
    except Exception as exc:
        return _research_observed_fallback(video_state, exc, log), "forensic-fallback"


def _editorial_oncelik_denetimi(editorial_state, log):
    state = _object_state_or_empty(editorial_state)
    options = state.get("story_options") if isinstance(state.get("story_options"), list) else []
    scored = []
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            continue
        try:
            score = float(option.get("toplam_oncelik"))
        except (TypeError, ValueError):
            continue
        scored.append((score, index, option))
    if not scored:
        state["_runtime_priority_audit"] = {"status": "no_scored_options"}
        log("⚠️ Editorial öncelik denetimi: puanlanmış hikâye adayı bulunamadı.")
        return state

    top_score, top_index, top_option = max(scored, key=lambda item: item[0])
    try:
        selected_index = int(state.get("selected_story_index"))
    except (TypeError, ValueError):
        selected_index = -1
    selected_score = next((score for score, index, _ in scored if index == selected_index), None)
    gap = top_score - selected_score if selected_score is not None else top_score
    mismatch = selected_index != top_index
    state["_runtime_priority_audit"] = {
        "status": "review" if mismatch else "aligned",
        "top_index": top_index,
        "top_name": str(top_option.get("isim") or ""),
        "top_category": str(top_option.get("kategori") or ""),
        "top_score": top_score,
        "selected_index": selected_index,
        "score_gap": round(gap, 2),
    }
    if mismatch:
        log(
            f"⚠️ Editorial öncelik sapması: seçilen index={selected_index}, en yüksek index={top_index} "
            f"({top_option.get('kategori') or 'kategori yok'}, fark={gap:.2f}). Reels ve QA ikinci denetimi uygulayacak."
        )
    else:
        log(f"✅ Editorial Türkiye ilgi önceliği doğrulandı: {top_option.get('kategori') or 'kategori yok'} | {top_score:.1f}/10")
    return state


def _editorial_calistir(router, video_state, fact_state, notes, log, ton=None):
    content = girdi_birlestir(durumu_metne_donustur('VIDEO STATE', video_state), durumu_metne_donustur('FACT LOCK', fact_state), notes or '')
    with _istek_profili(router, "uzun_metin"):
        result, model = _run_timed(
            log, "Editorial Brain (Gemini)",
            lambda: router.metin_uret(content, editorial_promptunu_olustur(ton), EDITORIAL_SCHEMA, log, arama_kullan=False),
        )
    return _editorial_oncelik_denetimi(result, log), model


def _caption_model(router, reels_state, fact_state, editorial_state, video_state, log, ton=None):
    content = girdi_birlestir(durumu_metne_donustur('REELS', reels_state), durumu_metne_donustur('FACT LOCK', fact_state), durumu_metne_donustur('EDITORIAL', editorial_state), durumu_metne_donustur('VIDEO', video_state))
    result, model = _run_timed(
        log, "Caption + Hashtag (Gemini)",
        lambda: router.metin_uret(content, caption_promptunu_olustur(ton), CAPTION_SCHEMA, log, arama_kullan=False),
    )
    return _caption_state_normalize(result), model


def _caption_calistir(router, reels_state, fact_state, editorial_state, video_state, log, ton=None):
    """Sosyal korumalı caption: boş/artifact çıktı kabul edilmez; tek kontrollü
    yeniden üretim + Fact Lock tabanlı güvenli fallback."""
    for attempt in range(SOCIAL_REGEN_MAX + 1):
        try:
            state, model = _caption_model(router, reels_state, fact_state, editorial_state, video_state, log, ton)
        except Exception as exc:
            log(f"⚠️ Caption üretimi hata verdi: {str(exc)[:160]}")
            state, model = {"reels_aciklamasi": "", "reels_hashtagleri": []}, "hata"
        description = _text((state or {}).get("reels_aciklamasi"))
        hashtags = sanitize_hashtags((state or {}).get("reels_hashtagleri"))
        if description and not looks_like_artifact(description) and hashtags:
            return {"reels_aciklamasi": description, "reels_hashtagleri": hashtags}, model
        if attempt < SOCIAL_REGEN_MAX:
            reason = "artifact/boş" if looks_like_artifact(description) else "eksik"
            log(f"⚠️ Caption {reason} döndü; tek kontrollü yeniden üretim ({attempt + 1}/{SOCIAL_REGEN_MAX}).")
    description, hashtags = caption_fallback(fact_state, editorial_state, video_state)
    log("⚠️ Caption modeli geçerli sosyal metin vermedi; Fact Lock tabanlı güvenli fallback kullanıldı.")
    return {"reels_aciklamasi": description, "reels_hashtagleri": hashtags}, "local-fallback"


def _threads_model(router, video_state, fact_state, editorial_state, log, ton=None):
    content = girdi_birlestir(durumu_metne_donustur('VIDEO', video_state), durumu_metne_donustur('FACT LOCK', fact_state), durumu_metne_donustur('EDITORIAL', editorial_state))
    result, model = _run_timed(
        log, "Threads (Gemini)",
        lambda: router.metin_uret(content, threads_promptunu_olustur(ton), THREADS_SCHEMA, log, arama_kullan=False),
    )
    return _threads_state_normalize(result), model


def _threads_calistir(router, video_state, fact_state, editorial_state, log, ton=None):
    """Sosyal korumalı threads: boş/artifact çıktı kabul edilmez; tek kontrollü
    yeniden üretim + Fact Lock tabanlı güvenli fallback."""
    for attempt in range(SOCIAL_REGEN_MAX + 1):
        try:
            state, model = _threads_model(router, video_state, fact_state, editorial_state, log, ton)
        except Exception as exc:
            log(f"⚠️ Threads üretimi hata verdi: {str(exc)[:160]}")
            state, model = {"threads_aciklamasi": ""}, "hata"
        text_value = _text((state or {}).get("threads_aciklamasi"))
        if text_value and not looks_like_artifact(text_value):
            return {"threads_aciklamasi": text_value}, model
        if attempt < SOCIAL_REGEN_MAX:
            reason = "artifact/boş" if looks_like_artifact(text_value) else "geçersiz"
            log(f"⚠️ Threads {reason} döndü; tek kontrollü yeniden üretim ({attempt + 1}/{SOCIAL_REGEN_MAX}).")
    text_value = threads_fallback(fact_state, editorial_state, video_state)
    log("⚠️ Threads modeli geçerli sosyal metin vermedi; Fact Lock tabanlı güvenli fallback kullanıldı.")
    return {"threads_aciklamasi": text_value}, "local-fallback"


# --- Ses modu katmanı ------------------------------------------------------------

def _explicit_voice_mode_from_notes(notes):
    """Kullanıcı notundan açık ses modu talebini çözer; yoksa '' döner."""
    text = str(notes or "").casefold().replace("_", " ")
    if re.search(r"\b(sen\s+seç|ai.{0,20}karar\s+ver|içeriğe\s+göre\s+seç|videoya\s+göre\s+seç)\b", text):
        return ""

    duo_pattern = r"\b(duo|iki\s+ses(?:li)?|çift\s+ses(?:li)?)\b"
    solo_pattern = r"\b(solo|tek\s+ses(?:li)?)\b"
    duo_negated = bool(re.search(duo_pattern + r".{0,18}\b(olmasın|istemiyorum|isteme)\b", text))
    solo_negated = bool(re.search(solo_pattern + r".{0,18}\b(olmasın|istemiyorum|isteme)\b", text))

    candidates = []
    for pattern, mode in (
        (r"\b(solo\s+female|sadece\s+kadın|yalnızca\s+kadın|tek\s+kadın\s+sesi)\b", "SOLO_FEMALE"),
        (r"\b(solo\s+male|sadece\s+erkek|yalnızca\s+erkek|tek\s+erkek\s+sesi)\b", "SOLO_MALE"),
    ):
        match = re.search(pattern, text)
        if match and not solo_negated:
            candidates.append((match.start(), mode))
    if not solo_negated:
        match = re.search(solo_pattern, text)
        if match:
            candidates.append((match.start(), "SOLO"))
    if not duo_negated:
        match = re.search(duo_pattern, text)
        if match:
            candidates.append((match.start(), "DUO"))
    return max(candidates, default=(-1, ""))[1]


def _mod_karari_al(editorial_state):
    karar = _object_state_or_empty(editorial_state).get('anlatim_modu_karari')
    return karar if isinstance(karar, dict) else {}


def _anlatim_modu_karari_ekle(router, video_state, fact_state, editorial_state, sure_saniye, ton, notes, log):
    editorial = dict(_object_state_or_empty(editorial_state))
    if not editorial:
        log('⚠️ Editorial state okunamadı; anlatım modu kararı atlandı.')
        return editorial_state
    if _explicit_voice_mode_from_notes(notes):
        log('🎚️ Kullanıcı notunda açık ses modu talebi var; AI mod kararı atlandı.')
        return editorial
    if _yakin_zamanda_asiri_yuk(router):
        # Karar verilemezse pipeline zaten varsayılan DUO ile ilerliyor; yoğunlukta
        # bu isteğe bağlı çağrı hiç yapılmaz.
        log('⏭️ Anlatım modu kararı atlandı: API yakın zamanda tam tur aşırı yük verdi; varsayılan mod kullanılacak.')
        return editorial
    with _hizli_basarislik(router), _istek_profili(router, "istege_bagli"):
        karar = _run_timed(
            log, "Anlatım modu kararı (Gemini)",
            lambda: anlatim_modu_karar_ver(router, video_state, fact_state, editorial, sure_saniye, ton, log),
        )
    if karar:
        editorial['anlatim_modu_karari'] = karar
    return editorial


# --- QA katmanı --------------------------------------------------------------------

def _qa_calistir(router, video_state, fact_state, editorial_state, reels_state, caption_state, threads_state, sure_saniye, log, duo_plan=None, duo_script=None, ton=None):
    content = girdi_birlestir(durumu_metne_donustur('VIDEO', video_state), durumu_metne_donustur('FACT LOCK', fact_state), durumu_metne_donustur('EDITORIAL', editorial_state), durumu_metne_donustur('REELS', reels_state), durumu_metne_donustur('DUO PLAN', duo_plan or {}), durumu_metne_donustur('DUO SCRIPT', duo_script or {}), durumu_metne_donustur('CAPTION', caption_state), durumu_metne_donustur('THREADS', threads_state), f'VIDEO SÜRESİ: {sure_saniye}', f'SEÇİLEN İÇERİK TÜRÜ: {ton or "dengeli"}')
    try:
        with _istek_profili(router, "uzun_metin"):
            result, model = _run_timed(
                log, "Final QA (Gemini)",
                lambda: router.metin_uret(content, qa_promptunu_olustur(ton), QA_SCHEMA, log, arama_kullan=False),
            )
    except Exception as exc:
        # QA API'si (tüm model+key + aşırı-yük tekrarları) geçici olarak
        # erişilemezse hazır TTS/render çöpe atılmaz: yapısal kontroller yine
        # uygulanır; model QA'sı atlandığı açıkça işaretlenir.
        log(f"⚠️ Final QA modeli geçici olarak erişilemedi; yapısal kontrollerle devam ediliyor: {type(exc).__name__}: {str(exc)[:160]}")
        return {"overall": "PASS", "regeneration_targets": [], "qa_unavailable": True, "reason": str(exc)[:200]}, "qa-unavailable"
    result = _object_state_or_empty(result)
    if not result:
        result = {"overall": "FAIL", "regeneration_targets": ["QA_PARSE_FAIL"]}
    return result, model


def _caption_eksikse_tamamla(router, caption_state, model_caption, reels_state, fact_state, editorial_state, video_state, log, ton):
    """Metadata ajanı açıklama/hashtag vermediyse Caption ajanı ile tamamlar."""
    caption_state = _caption_state_normalize(caption_state)
    if caption_state.get("reels_aciklamasi") and caption_state.get("reels_hashtagleri"):
        return caption_state, model_caption
    log("⚠️ Metadata ajanı eksik caption/hashtag döndü; Caption ajanı ile tamamlanıyor.")
    try:
        yeni, model = _caption_calistir(router, reels_state, fact_state, editorial_state, video_state, log, ton)
    except Exception as exc:
        log(f"⚠️ Caption tamamlama başarısız; worker sosyal fallback'i devreye girecek: {str(exc)[:160]}")
        return caption_state, model_caption
    yeni = _caption_state_normalize(yeni)
    return {
        "reels_aciklamasi": caption_state.get("reels_aciklamasi") or yeni.get("reels_aciklamasi", ""),
        "reels_hashtagleri": caption_state.get("reels_hashtagleri") or yeni.get("reels_hashtagleri", []),
    }, model


def _duo_qa_nonblocking_mi(ses_basarili, ses_modu, ses_dosyasi, duo_script, log):
    """QA yalnızca DUO script katmanını işaretlerken geçerli bir DUO TTS varsa
    render boğulmaz: duzgu bir scripti yeniden üretmek API yoğunluğunda üretimi
    durduruyordu. Tek sesli dosya asla bu yoldan geçemez (ses_modu == DUO şartı)."""
    if ses_modu != "DUO" or not ses_basarili or not ses_dosyasi or not os.path.exists(ses_dosyasi):
        return False
    issues = duo_script.get("conversation_quality_issues") if isinstance(duo_script, dict) else ["unknown_script"]
    if issues:
        return False
    if callable(log):
        log("⚠️ QA yalnızca Duo script katmanını işaretledi; geçerli TTS bulunduğu için render güvenli biçimde devam ediyor.")
    return True


def _qa_regeneration_loop(router, video_state, fact_state, editorial_state, reels_state, caption_state, threads_state, duo_plan, duo_script, sure_saniye, ton, legacy_voice, log, voice_initial_instruction='', production_notes='', ses_modu_notlari=None):
    """Agentic üretim + caption/threads + final QA (maks 1 kontrollü yenileme).

    Dönüş (15'liuplü, SÜREKLİ KULLANILAN SIRALAMA):
      (reels_state, model_reels, duo_plan, duo_script, ses_basarili,
       kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, threads_state,
       qa_state, qa_rounds, model_caption, model_threads, qa_pass)
    """
    qa_state = {}
    qa_rounds = 0
    ses_basarili = False
    kullanilan_ses_modeli = None
    ses_modu = 'LEGACY'
    ses_dosyasi = ''

    # Anlatım Modu Kararını Al
    mod_karari = _mod_karari_al(editorial_state)

    # Agentic Üretim Başlatılıyor
    reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, metadata_state = agentic_icerik_uretimi(
        router, video_state, fact_state, editorial_state, sure_saniye, ton, legacy_voice, log, mod_karari=mod_karari
    )

    # Metadata'dan Caption'ı al
    caption_state = _caption_state_normalize(metadata_state)
    model_caption = "agentic"
    caption_state, model_caption = _caption_eksikse_tamamla(router, caption_state, model_caption, reels_state, fact_state, editorial_state, video_state, log, ton)

    # Threads
    threads_state, model_threads = _threads_calistir(router, video_state, fact_state, editorial_state, log, ton)

    for qa_round in range(MAX_QA_REGEN + 1):
        qa_rounds = qa_round
        qa_state, _ = _qa_calistir(router, video_state, fact_state, editorial_state, reels_state, caption_state, threads_state, sure_saniye, log, duo_plan, duo_script, ton)
        if not isinstance(qa_state, dict):
            qa_state = _object_state_or_empty(qa_state)

        targets_raw = qa_state.get('regeneration_targets') or []
        if isinstance(targets_raw, str):
            targets_raw = [targets_raw]
        if not isinstance(targets_raw, list):
            targets_raw = []
        targets = [str(x).strip().upper() for x in targets_raw if str(x).strip()]
        supported_targets = [x for x in targets if x in QA_REGEN_TARGETS]
        overall = str(qa_state.get('overall') or qa_state.get('status') or '').strip().upper()

        expected_mode = _beklenen_gercek_mod(duo_plan, duo_script)
        if overall == 'PASS' and not supported_targets:
            if expected_mode == "DUO" and ses_modu != "DUO":
                overall = "FAIL"
                supported_targets = ["DUO_SCRIPT_FAIL"]
                qa_state["overall"] = "FAIL"
                qa_state["regeneration_targets"] = supported_targets
            elif expected_mode == "DUO" and (not ses_basarili or not ses_dosyasi or not os.path.exists(ses_dosyasi)):
                overall = "FAIL"
                supported_targets = ["DUO_SCRIPT_FAIL"]
                qa_state["overall"] = "FAIL"
                qa_state["regeneration_targets"] = supported_targets
            else:
                return reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, threads_state, qa_state, qa_rounds, model_caption, model_threads, True

        if not supported_targets:
            break
        if qa_round >= MAX_QA_REGEN:
            break

        target_set = set(supported_targets)
        creative_needed = bool(target_set & {'VOICEOVER_FAIL', 'COVER_FAIL', 'DUO_SCRIPT_FAIL'})
        downstream_threads = 'THREADS_FAIL' in target_set
        caption_only = 'CAPTION_FAIL' in target_set and not creative_needed

        log(f"⚠️ QA başarısız bulundu ({', '.join(supported_targets)}). İlgili agentic katmanlar yeniden üretiliyor...")

        if creative_needed:
            onceki = (reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, model_caption)
            try:
                reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, metadata_state = agentic_icerik_uretimi(
                    router, video_state, fact_state, editorial_state, sure_saniye, ton, legacy_voice, log, mod_karari=mod_karari
                )
                caption_state = _caption_state_normalize(metadata_state)
                model_caption = "agentic"
                caption_state, model_caption = _caption_eksikse_tamamla(router, caption_state, model_caption, reels_state, fact_state, editorial_state, video_state, log, ton)
            except Exception as exc:
                # QA yenilemesi API yoğunluğunda düşerse elde kalan geçerli
                # üretim çöpe atılmaz; önceki sürüm korunur.
                log(f"⚠️ QA yenilemesi üretilemedi; önceki agentic çıktı korunuyor: {type(exc).__name__}: {str(exc)[:160]}")
                reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, model_caption = onceki
        elif caption_only:
            try:
                caption_state, model_caption = _caption_calistir(router, reels_state, fact_state, editorial_state, video_state, log, ton)
            except Exception:
                pass
            caption_state = _caption_state_normalize(caption_state)

        if downstream_threads:
            try:
                threads_state, model_threads = _threads_calistir(router, video_state, fact_state, editorial_state, log, ton)
            except Exception:
                threads_state = {"threads_aciklamasi": ""}
            threads_state = _threads_state_normalize(threads_state)

    # Yenileme bitti ama QA yalnızca DUO scriptini işaretledi ve elinde geçerli
    # bir DUO TTS varsa render'ı boğma (tek sesli dosya bu yoldan geçemez).
    kalan = {str(x).strip().upper() for x in (qa_state.get('regeneration_targets') or []) if str(x).strip().upper()}
    if kalan == {"DUO_SCRIPT_FAIL"} and _duo_qa_nonblocking_mi(ses_basarili, ses_modu, ses_dosyasi, duo_script, log):
        qa_state = dict(qa_state)
        qa_state["overall"] = "PASS"
        qa_state["regeneration_targets"] = []
        qa_state["duo_nonblocking_fallback"] = True
        return reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, threads_state, qa_state, qa_rounds, model_caption, model_threads, True

    return reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, threads_state, qa_state, qa_rounds, model_caption, model_threads, False


# --- Halka açık girişler ------------------------------------------------------------

def pipeline_calistir(router, video_bytes, mime_type, temp_input_video, video_analiz_notlari, metin_uretim_notlari, sure_saniye, icerik_tonu, secilen_ses_ingilizce, log_ekle, ilerlemeyi_guncelle=None):
    sure_saniye = _resolve_video_duration_strict(sure_saniye, temp_input_video)
    if sure_saniye is None:
        state = {"duration_error": "video_duration_unavailable"}
        log_ekle("❌ Video süresi güvenilir biçimde okunamadı; pipeline güvenli şekilde durduruldu.")
        return _payload(
            {}, {}, {}, False, "", secilen_ses_ingilizce if isinstance(secilen_ses_ingilizce, str) else "Autonoe",
            "hata", None, "hata", "Bilinmiyor", 0, "", temp_input_video, {}, {}, {}, {},
            {"overall": "FAIL", "regeneration_targets": ["DURATION_FAIL"], "reason": "video_duration_unavailable"},
            False, state
        )

    state = {'content_tone': str(icerik_tonu or 'dengeli').strip().lower()}
    log_ekle(f"🎯 İçerik türü runtime kilidi aktif: {state['content_tone']}")
    _ilerleme(ilerlemeyi_guncelle, 1); log_ekle('🎥 Video analiz ediliyor (Forensic)...')
    video_state, _ = _forensic_analiz_calistir(router, video_bytes, mime_type, video_analiz_notlari, sure_saniye, log_ekle); state['video_state'] = video_state
    _ilerleme(ilerlemeyi_guncelle, 2); log_ekle('🔎 Gerçekler doğrulanıyor (Research / Fact Lock)...')
    fact_state, _ = _research_calistir(router, video_state, log_ekle); state['fact_state'] = fact_state
    _ilerleme(ilerlemeyi_guncelle, 3); log_ekle('🧠 Hikâye seçiliyor (Editorial Brain)...')
    editorial_state, _ = _editorial_calistir(router, video_state, fact_state, metin_uretim_notlari, log_ekle, icerik_tonu)
    log_ekle('🎚️ Anlatım modu belirleniyor (tek ses / çift ses)...')
    editorial_state = _anlatim_modu_karari_ekle(router, video_state, fact_state, editorial_state, sure_saniye, icerik_tonu, metin_uretim_notlari, log_ekle)
    state['editorial_state'] = editorial_state; state['anlatim_modu_karari'] = _mod_karari_al(editorial_state)
    _ilerleme(ilerlemeyi_guncelle, 4); log_ekle('🎙️ Agentic Viral Üretim Döngüsü Başlatılıyor (4 Ajan)...')
    legacy_voice = secilen_ses_ingilizce if isinstance(secilen_ses_ingilizce, str) and secilen_ses_ingilizce.strip() else 'Autonoe'
    reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, threads_state, qa_state, qa_rounds, model_caption, model_threads, qa_pass = _qa_regeneration_loop(
        router, video_state, fact_state, editorial_state, {}, {}, {}, {}, {}, sure_saniye, icerik_tonu, legacy_voice, log_ekle, production_notes=metin_uretim_notlari
    )
    state['reels_state'] = reels_state; state['duo_plan'] = duo_plan; state['duo_script'] = duo_script; state['ses_modu'] = ses_modu; state['qa_regeneration_rounds'] = qa_rounds; state['qa_pass'] = qa_pass
    if ses_basarili and ses_dosyasi and os.path.exists(ses_dosyasi):
        stable_tts = gecici_dosya_yolu('pipeline_tts_stable', 'wav')
        try:
            shutil.copy2(ses_dosyasi, stable_tts)
            ses_dosyasi = stable_tts
            state['ses_dosyasi_son'] = ses_dosyasi
            log_ekle('🔒 TTS dosyası pipeline sonuna kadar korunmak üzere sabitlendi.')
        except Exception as exc:
            log_ekle(f'⚠️ TTS sabitleme başarısız; mevcut dosya kullanılmaya devam edilecek: {str(exc)[:150]}')

    _ilerleme(ilerlemeyi_guncelle, 5); state['caption_state'] = caption_state
    _ilerleme(ilerlemeyi_guncelle, 6); state['threads_state'] = threads_state
    _ilerleme(ilerlemeyi_guncelle, 7); state['qa_state_final'] = qa_state
    if not qa_pass:
        _ilerleme(ilerlemeyi_guncelle, 8); log_ekle('❌ QA PASS alınamadı; TTS/render aşaması güvenli biçimde durduruldu.')
        return _payload(reels_state, caption_state, threads_state, False, '', legacy_voice,
                        model_reels, kullanilan_ses_modeli, model_threads, ses_modu, qa_rounds,
                        '', temp_input_video, fact_state, editorial_state, duo_plan, duo_script,
                        qa_state, False, state)

    if ses_basarili and (not ses_dosyasi or not os.path.exists(ses_dosyasi)):
        log_ekle('❌ Pipeline tamamlanamadı: render için doğrulanmış TTS dosyası yok.')
        return _payload(reels_state, caption_state, threads_state, False, '', legacy_voice,
                        model_reels, kullanilan_ses_modeli, model_threads, ses_modu, qa_rounds,
                        '', temp_input_video, fact_state, editorial_state, duo_plan, duo_script,
                        qa_state, qa_pass, state, input_media={}, output_media={})

    _ilerleme(ilerlemeyi_guncelle, 8); log_ekle(f'🎧 Hazır ses kullanılıyor ({ses_modu} → {_ses_modu_sesi(ses_modu)}).')
    _ilerleme(ilerlemeyi_guncelle, 9); log_ekle('🎬 Videoya AI sesi ekleniyor (FFmpeg)...')
    output = gecici_dosya_yolu('output', 'mp4')
    render_ok = ses_basarili and _run_timed(
        log_ekle, "FFmpeg video + TTS render",
        lambda: video_ve_sesi_birlestir(temp_input_video, ses_dosyasi, output, log_ekle),
    )
    final = output if render_ok and os.path.exists(output) else ''
    input_media = medya_raporu(temp_input_video, 'INPUT FINAL', log_ekle) if os.path.exists(temp_input_video) else {}
    output_media = medya_raporu(final, 'OUTPUT FINAL', log_ekle) if final else {}
    if final:
        log_ekle('🏁 Pipeline tamamlandı.')
    else:
        log_ekle('❌ Pipeline tamamlanamadı: FFmpeg final video üretemedi.')
    return _payload(reels_state, caption_state, threads_state, ses_basarili, ses_dosyasi,
                    legacy_voice, model_reels, kullanilan_ses_modeli, model_threads, ses_modu,
                    qa_rounds, final, temp_input_video, fact_state, editorial_state, duo_plan,
                    duo_script, qa_state, qa_pass, state, input_media=input_media,
                    output_media=output_media)


def metin_pipeline_calistir(router, metin, icerik_tonu, secilen_ses_ingilizce, log_ekle, ilerlemeyi_guncelle=None, sure_saniye=30):
    metin = (metin or '').strip()
    if not metin:
        raise ValueError('Metin girdisi boş.')

    sure_saniye = _coerce_positive_float(sure_saniye)
    if sure_saniye is None:
        raise ValueError("Metin modu için geçerli bir sure_saniye gerekli (pozitif sayı).")

    state = {'content_tone': str(icerik_tonu or 'dengeli').strip().lower()}
    log_ekle(f"🎯 İçerik türü runtime kilidi aktif: {state['content_tone']}")
    video_state = {'video_identity': {'brand': 'UNKNOWN', 'exact_model': 'UNKNOWN', 'confidence': 'unknown', 'source': 'telegram_text'}, 'observed_facts': [metin], 'unknowns': [], 'possible_inference': [], 'viral_arastirma_ihtiyaclari': ['Metindeki araç/konu kimliğini ve güncel iddiaları doğrula.'], 'visual_opportunities': ['Metin tabanlı üretim; video görsel zaman çizelgesi yok.'], 'timeline': []}
    state['video_state'] = video_state
    _ilerleme(ilerlemeyi_guncelle, 1, '📝 Metin girdisi'); log_ekle('📝 Metin girdisi işleniyor (video analizi atlanıyor)...')
    _ilerleme(ilerlemeyi_guncelle, 2, '🔎 Research / Fact Lock'); fact_state, _ = _research_calistir(router, video_state, log_ekle); state['fact_state'] = fact_state
    _ilerleme(ilerlemeyi_guncelle, 3, '🧠 Editorial Brain'); editorial_state, _ = _editorial_calistir(router, video_state, fact_state, metin, log_ekle, icerik_tonu)
    log_ekle('🎚️ Anlatım modu belirleniyor (tek ses / çift ses)...')
    # Metin modda kullanıcı metni = üretim notu; açık ses modu talebi burada aranır.
    editorial_state = _anlatim_modu_karari_ekle(router, video_state, fact_state, editorial_state, sure_saniye, icerik_tonu, metin, log_ekle)
    state['editorial_state'] = editorial_state; state['anlatim_modu_karari'] = _mod_karari_al(editorial_state)
    _ilerleme(ilerlemeyi_guncelle, 4, '🎙️ Agentic Viral Üretim Döngüsü Başlatılıyor (4 Ajan)...'); legacy_voice = secilen_ses_ingilizce if isinstance(secilen_ses_ingilizce, str) and secilen_ses_ingilizce.strip() else 'Autonoe'
    reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, threads_state, qa_state, qa_rounds, model_caption, model_threads, qa_pass = _qa_regeneration_loop(
        router, video_state, fact_state, editorial_state, {}, {}, {}, {}, {}, sure_saniye, icerik_tonu, legacy_voice, log_ekle, production_notes=metin, ses_modu_notlari=''
    )
    state['reels_state'] = reels_state; state['duo_plan'] = duo_plan; state['duo_script'] = duo_script; state['ses_modu'] = ses_modu; state['qa_regeneration_rounds'] = qa_rounds; state['qa_pass'] = qa_pass
    state['caption_state'] = _caption_state_normalize(caption_state); state['threads_state'] = _threads_state_normalize(threads_state); state['qa_state_final'] = qa_state
    _ilerleme(ilerlemeyi_guncelle, 5, '📝 Caption + hashtag'); _ilerleme(ilerlemeyi_guncelle, 6, '🧵 Threads'); _ilerleme(ilerlemeyi_guncelle, 7, '🔍 QA')
    _ilerleme(ilerlemeyi_guncelle, 8, '🎧 Ses üretiliyor...')
    if not qa_pass:
        log_ekle('❌ QA PASS alınamadı; text-only ses gönderimi durduruldu.')
        ses_basarili = False; ses_dosyasi = ''
    elif ses_basarili:
        log_ekle(f'🎧 Hazır ses kullanılıyor ({ses_modu} → {_ses_modu_sesi(ses_modu)}); tekrar TTS üretilmiyor.')
    else:
        log_ekle('❌ Güvenli TTS üretilemedi.')
    log_ekle('🏁 Metin üretimi tamamlandı; video render atlandı.')
    return _payload(reels_state, caption_state, threads_state, ses_basarili, ses_dosyasi,
                    legacy_voice, model_reels, kullanilan_ses_modeli, model_threads, ses_modu,
                    qa_rounds, '', '', fact_state, editorial_state, duo_plan, duo_script,
                    qa_state, qa_pass, state, mode='text')
