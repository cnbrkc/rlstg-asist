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
from concurrent.futures import ThreadPoolExecutor

from core.agentic import (
    _asiri_yukte_atla_mi, _istek_profili, _beklenen_gercek_mod, _coerce_positive_float,
    _istege_bagli_router_baglami, _kelime_sayisi, _reels_kelime_ayarlarini_hazirla,
    _run_timed, _ses_sure_uyumlu_mu,
    _object_state_or_empty,
    agentic_icerik_uretimi, kapaklari_yeniden_uret,
)
from core.web_search import otv_ek_arastirma, web_arastirma_yap
from core.otv_kilidi import (
    kapaklari_otv_kilidine_cek,
    otv_kilidi_hesapla,
    otv_tutarlilik_sorunlari,
    sosyal_metni_otv_kilidine_cek,
    vergi_kilidi_talimati,
    vergi_kilidini_uygula,
)
from core.config import KELIME_HIZI_ORANI, PIPELINE_ADIMLARI
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
# Telegram video caption sınırı 1024 karakter; açıklama + 5 hashtag güvenli
# biçimde sığmalı ki worker sonradan metni kesmek ZORUNDA KALMASIN
# (caption_prompt.txt'taki 700-850 hedefinin kod tarafındaki sert tavanı).
CAPTION_AZAM_KARAKTER = 900
HASHTAG_AZAM_SAYI = 5
# Seslendirme/video yenilemesinden sonra kalan hatalar YALNIZ sosyal katmandaysa
# (caption / threads / kapak metni — videoyu değiştirmeyen, ucuz çağrılar) QA
# geri bildirimiyle bir ek yenileme turu daha yapılır; kalite teslim edilmeden
# önce düzeltilmeye çalışılır.
SOCIAL_QA_EXTRA_REGEN = 1
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


def _caption_kurallari_uygula(deger, hashtags):
    """Deterministik sosyal kurallar — model sözünü tutmazsa KOD tutar:
      * açıklama: CAPTION_AZAM_KARAKTER tavanı (kelime sınırında kesim, sarkık
        noktalama temizliği) — Telegram 1024'lük video caption sınırına 5
        hashtagle birlikte sığacak payı korur;
      * hashtag: tekrarsız, HASHTAG_AZAM_SAYI tavanı (sıra korunur).
    Prompt'taki '700-850 karakter, TAM 5 hashtag' kuralının kod tarafındaki
    güvencesidir; kısa metin burada UZATILMAZ (QA caption_check + kontrollü
    yenileme mekanizmasıyla işlenir)."""
    deger = str(deger or "").strip()
    if len(deger) > CAPTION_AZAM_KARAKTER:
        kesim = deger[:CAPTION_AZAM_KARAKTER]
        if " " in kesim:
            kesim = kesim.rsplit(" ", 1)[0]
        deger = kesim.rstrip(" ,;:.!?…-")
    etiketler, gorulen = [], set()
    for t in (hashtags or []):
        t = str(t or "").strip()
        if not t:
            continue
        anahtar = t.lstrip("#").casefold()
        if not anahtar or anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        etiketler.append(t)
        if len(etiketler) >= HASHTAG_AZAM_SAYI:
            break
    return deger, etiketler


def _caption_state_normalize(value, log=None):
    """Metadata ajanı (`reels_aciklama`/`reels_hashtag`) ve Caption ajanı
    (`reels_aciklamasi`/`reels_hashtagleri`) çıktıları aynı forma indirgenir;
    deterministic kurallar (900 karakter / 5 hashtag tavanı) uygulanır."""
    parsed = _json_object_or_none(value)
    if parsed is not None:
        aciklama = parsed.get("reels_aciklamasi") or parsed.get("reels_aciklama") or ""
        hashtags = parsed.get("reels_hashtagleri")
        if not isinstance(hashtags, list) or not hashtags:
            hashtags = parsed.get("reels_hashtag")
        ham = hashtags if isinstance(hashtags, list) else []
        ham_deger = str(aciklama or "").strip()
        deger, etiketler = _caption_kurallari_uygula(aciklama, ham)
        ham_adet = len([x for x in ham if str(x or "").strip()])
        if callable(log) and (deger != ham_deger or len(etiketler) < ham_adet):
            log(f"✂️ Caption kuralları uygulandı: açıklama {len(ham_deger)}→{len(deger)} karakter | hashtag {ham_adet}→{len(etiketler)} (tavan {CAPTION_AZAM_KARAKTER} karakter / {HASHTAG_AZAM_SAYI} etiket).")
        return {
            "reels_aciklamasi": deger,
            "reels_hashtagleri": etiketler,
        }
    if isinstance(value, str) and value.strip():
        deger, _ = _caption_kurallari_uygula(value, [])
        return {"reels_aciklamasi": deger, "reels_hashtagleri": []}
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
            result, model = _run_timed(
                log, "Research / Fact Lock (Agentic Web + Gemini Analiz)",
                lambda: router.metin_uret(content, research_promptunu_olustur(), FACT_LOCK_SCHEMA, log, arama_kullan=False),
            )
    except Exception as exc:
        result, model = _research_observed_fallback(video_state, exc, log), "forensic-fallback"
        return vergi_kilidini_uygula(result, otv_kilidi_hesapla(result, web_sonuclari, video_state), log), model

    result = _object_state_or_empty(result)
    kilit = otv_kilidi_hesapla(result, web_sonuclari, video_state)
    if kilit.get("durum") != "KESIN":
        ek = otv_ek_arastirma(video_state, log)
        if ek:
            web_sonuclari = f"{web_sonuclari}\n\n{ek}".strip()
            kilit = otv_kilidi_hesapla(result, web_sonuclari, video_state)
    return vergi_kilidini_uygula(result, kilit, log), model


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
    content = girdi_birlestir(durumu_metne_donustur('VIDEO STATE', video_state), durumu_metne_donustur('FACT LOCK', fact_state), notes or '', vergi_kilidi_talimati(fact_state))
    with _istek_profili(router, "uzun_metin"):
        result, model = _run_timed(
            log, "Editorial Brain (Gemini)",
            lambda: router.metin_uret(content, editorial_promptunu_olustur(ton), EDITORIAL_SCHEMA, log, arama_kullan=False),
        )
    return _editorial_oncelik_denetimi(result, log), model


def _qa_duzeltme_talimati(katman, qa_geri_bildirimi):
    if not qa_geri_bildirimi:
        return ""
    return (
        f"\n\n🚨 FINAL QA BU {katman} METNİNİN ÖNCEKİ SÜRÜMÜNÜ REDDETTİ. Aşağıdaki sorunların HEPSİNİ düzelt; "
        "Fact Lock'ta OBSERVED/VERIFIED olmayan hiçbir iddia, rakam veya özellik kullanma:\n"
        f"{qa_geri_bildirimi}"
    )


def _caption_model(router, reels_state, fact_state, editorial_state, video_state, log, ton=None, qa_geri_bildirimi=""):
    content = girdi_birlestir(durumu_metne_donustur('REELS', reels_state), durumu_metne_donustur('FACT LOCK', fact_state), durumu_metne_donustur('EDITORIAL', editorial_state), durumu_metne_donustur('VIDEO', video_state))
    prompt = caption_promptunu_olustur(ton) + vergi_kilidi_talimati(fact_state) + _qa_duzeltme_talimati("CAPTION", qa_geri_bildirimi)
    result, model = _run_timed(
        log, "Caption + Hashtag (Gemini)",
        lambda: router.metin_uret(content, prompt, CAPTION_SCHEMA, log, arama_kullan=False),
    )
    return _caption_state_normalize(result, log), model


def _caption_calistir(router, reels_state, fact_state, editorial_state, video_state, log, ton=None, qa_geri_bildirimi=""):
    """Sosyal korumalı caption: boş/artifact çıktı kabul edilmez; tek kontrollü
    yeniden üretim + Fact Lock tabanlı güvenli fallback."""
    for attempt in range(SOCIAL_REGEN_MAX + 1):
        try:
            state, model = _caption_model(router, reels_state, fact_state, editorial_state, video_state, log, ton, qa_geri_bildirimi)
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


def _threads_model(router, video_state, fact_state, editorial_state, log, ton=None, qa_geri_bildirimi=""):
    content = girdi_birlestir(durumu_metne_donustur('VIDEO', video_state), durumu_metne_donustur('FACT LOCK', fact_state), durumu_metne_donustur('EDITORIAL', editorial_state), vergi_kilidi_talimati(fact_state))
    prompt = threads_promptunu_olustur(ton) + _qa_duzeltme_talimati("THREADS", qa_geri_bildirimi)
    result, model = _run_timed(
        log, "Threads (Gemini)",
        lambda: router.metin_uret(content, prompt, THREADS_SCHEMA, log, arama_kullan=False),
    )
    return _threads_state_normalize(result), model


def _threads_calistir(router, video_state, fact_state, editorial_state, log, ton=None, qa_geri_bildirimi=""):
    """Sosyal korumalı threads: boş/artifact çıktı kabul edilmez; tek kontrollü
    yeniden üretim + Fact Lock tabanlı güvenli fallback."""
    for attempt in range(SOCIAL_REGEN_MAX + 1):
        try:
            state, model = _threads_model(router, video_state, fact_state, editorial_state, log, ton, qa_geri_bildirimi)
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
    if _asiri_yukte_atla_mi(router):
        # Yalnız ISTEGE_BAGLI_ASIRI_YUK_ATLA=1 iken (hız-öncelikli operasyon modu).
        log('⏭️ Anlatım modu kararı atlandı: API yakın zamanda tam tur aşırı yük verdi; varsayılan mod kullanılacak.')
        return editorial
    # Karar arka planda (Detective/Hook ile paralel) çalıştığı için aşırı-yük
    # tekrar denemesi kritik yolu uzatmaz; kalite için açık tutulur.
    with _istege_bagli_router_baglami(router):
        karar = _run_timed(
            log, "Anlatım modu kararı (Gemini)",
            lambda: anlatim_modu_karar_ver(router, video_state, fact_state, editorial, sure_saniye, ton, log),
        )
    if karar:
        editorial['anlatim_modu_karari'] = karar
    return editorial


def _anlatim_modu_karari_guvenli(router, video_state, fact_state, editorial_state, sure_saniye, ton, notes, log):
    """Arka plan kolu için: hata olursa editorial değişmeden döner (varsayılan DUO)."""
    try:
        return _anlatim_modu_karari_ekle(router, video_state, fact_state, editorial_state, sure_saniye, ton, notes, log)
    except Exception as exc:
        log(f"⚠️ Anlatım modu kararı alınamadı; varsayılan mod (DUO) kullanılacak: {type(exc).__name__}: {str(exc)[:160]}")
        return editorial_state


def _anlatim_modu_arka_planda(router, video_state, fact_state, editorial_state, sure_saniye, ton, notes, log):
    """Anlatım modu kararı yalnız Script Writer'ı etkiler: Detective + Hook ile
    EŞZAMANLI hesaplanır (Eylül 2026 logunda seri akışta 27 sn bekletti)."""
    log('🎚️ Anlatım modu belirleniyor (tek ses / çift ses) — Detective/Hook ile paralel...')
    return _arka_planda(_anlatim_modu_karari_guvenli, router, video_state, fact_state, editorial_state, sure_saniye, ton, notes, log)


# --- QA katmanı --------------------------------------------------------------------

# QA modelinin döndürebildiği hedef adları → desteklenen yenileme hedefleri.
# Eylül 2026 üretim durması: QA promptu FACT_FAIL hedefini açıkça tanımlıyor
# ama loop bunu desteklenmeyen hedef sayıp HİÇ yenileme yapmadan ve NEDENİNİ
# loglamadan render'ı durduruyordu (9 dk üretim → video yok).
QA_TARGET_ALIASES = {
    "FACT_FAIL": "FACT",  # kapsamı gerekçeden çözülür (bkz. _fact_kapsami)
    "MODEL_FAIL": "FACT",
    "VIDEO_FAIL": "FACT",
    "CURRENT_DATA_FAIL": "FACT",
    "HOOK_FAIL": "VOICEOVER_FAIL",
    "LENGTH_FAIL": "VOICEOVER_FAIL",
    "TTS_FAIL": "VOICEOVER_FAIL",
    "REPETITION_FAIL": "VOICEOVER_FAIL",
    "TONE_FAIL": "VOICEOVER_FAIL",
    "VIRAL_PRIORITY_FAIL": "VOICEOVER_FAIL",
    "BRAND_FAIL": "VOICEOVER_FAIL",
    "SCRIPT_FAIL": "VOICEOVER_FAIL",
    "HASHTAG_FAIL": "CAPTION_FAIL",
    "VISUAL_MATCH_FAIL": "COVER_FAIL",
    "DUO_FAIL": "DUO_SCRIPT_FAIL",
}
# Tekil kontrol alanı → hedef (hedef listesi boş/eksik geldiğinde kullanılır).
QA_CHECK_TARGETS = {
    "fact_check": "FACT", "model_check": "FACT", "video_check": "FACT", "current_data_check": "FACT",
    "hook_check": "VOICEOVER_FAIL", "length_check": "VOICEOVER_FAIL", "tts_check": "VOICEOVER_FAIL",
    "repetition_check": "VOICEOVER_FAIL", "brand_check": "VOICEOVER_FAIL", "tone_check": "VOICEOVER_FAIL",
    "viral_priority_check": "VOICEOVER_FAIL",
    "cover_check": "COVER_FAIL", "visual_match_check": "COVER_FAIL",
    "caption_check": "CAPTION_FAIL", "hashtag_check": "CAPTION_FAIL",
    "threads_check": "THREADS_FAIL",
    "duo_check": "DUO_SCRIPT_FAIL",
}
QA_FACT_CHECKS = ("fact_check", "model_check", "video_check", "current_data_check")
QA_EMPTY_TARGETS = {"", "NONE", "YOK", "-", "N/A", "NA", "NULL", "PASS", "[]"}
# Videonun kendisini (ses + görüntü) değiştirmeyen hedefler: yenilemeden sonra
# hâlâ işaretliyse render durdurulmaz, raporda uyarı olarak kalır.
QA_SOCIAL_NONBLOCKING_TARGETS = {"CAPTION_FAIL", "THREADS_FAIL", "COVER_FAIL"}


def _qa_check_fail_mi(value):
    return bool(re.match(r"\s*(FAIL|BAŞARISIZ|❌)", str(value or ""), re.IGNORECASE))


def _qa_overall_coz(qa_state):
    ham = str(qa_state.get('overall') or qa_state.get('status') or '').strip().upper()
    m = re.match(r"\W*(PASS|FAIL)", ham)
    return m.group(1) if m else ham


def _fact_kapsami(gerekceler):
    """Gerçeklik hatasının hangi çıktıda olduğunu gerekçeden çözer. Belirsizse
    seslendirme (videoya gömülü, en kritik katman) yenilenir."""
    metin = " ".join(str(x or "") for x in gerekceler).casefold()
    hedefler = set()
    if re.search(r"caption|açıklama|aciklama|hashtag", metin):
        hedefler.add("CAPTION_FAIL")
    if "threads" in metin:
        hedefler.add("THREADS_FAIL")
    if not hedefler or re.search(r"seslendirme|senaryo|script|replik|diyalog|duo|hook|kanca|kapak|metin|ses\b", metin):
        hedefler.add("VOICEOVER_FAIL")
    return hedefler


def _uzunluk_uygun_mu(reels_state, sure_saniye, ses_dosyasi=None):
    """QA'nın length_check FAIL'i yalnız deterministik ölçümler KESİN uygunluk
    gösteriyorsa yok sayılır: kelime sayısı Script Writer'a verilen SIKI aralıkta
    (hedef ±%10) VE — ses dosyası varsa — gerçek TTS/video süre oranı 0.85-1.15
    içinde. Gevşek tolerans (±%20) burada kullanılmaz; aksi hâlde QA'nın haklı
    uzunluk itirazı bastırılabilirdi."""
    try:
        _hedef, minimum, maksimum, _, _ = _reels_kelime_ayarlarini_hazirla(sure_saniye, KELIME_HIZI_ORANI)
    except Exception:
        return False
    adet = _kelime_sayisi(_object_state_or_empty(reels_state).get("seslendirme_metni", ""))
    if adet <= 0 or not (minimum <= adet <= maksimum):
        return False
    if ses_dosyasi and os.path.exists(ses_dosyasi):
        try:
            uyumlu, ses_suresi, _oran = _ses_sure_uyumlu_mu(ses_dosyasi, sure_saniye)
        except Exception:
            return False
        if ses_suresi > 0 and not uyumlu:
            return False
    return True


def _qa_sonucunu_coz(qa_state, reels_state, sure_saniye, log, ses_dosyasi=None):
    """QA çıktısını (overall, desteklenen hedefler, FAIL kontrolleri) üçlüsüne indirger.

    * overall 'PASS: ...' / 'FAIL - ...' gibi serbest yazımlarda da doğru okunur.
    * FACT_FAIL, HASHTAG_FAIL, TONE_FAIL... gibi hedefler desteklenen yenileme
      hedeflerine eşlenir (FACT kapsamı gerekçeden çözülür).
    * overall FAIL ama hedef listesi boş/tanınmıyorsa tekil kontrol alanlarından
      (fact_check: 'FAIL: ...') hedef türetilir.
    * length_check FAIL, deterministik ölçüm (sıkı kelime aralığı + gerçek TTS
      süresi) uzunluğun kesin uygun olduğunu gösteriyorsa yok sayılır.
    """
    overall = _qa_overall_coz(qa_state)
    failing = {k: str(v) for k, v in qa_state.items() if k in QA_CHECK_TARGETS and _qa_check_fail_mi(v)}
    if "length_check" in failing and _uzunluk_uygun_mu(reels_state, sure_saniye, ses_dosyasi):
        log("📏 QA length_check FAIL dedi ama kelime sayısı hedef aralıkta ve gerçek TTS süresi videoya uygun; bu kontrol yok sayıldı.")
        failing.pop("length_check")
        qa_state["length_check_override"] = True

    targets_raw = qa_state.get('regeneration_targets') or []
    if isinstance(targets_raw, str):
        targets_raw = re.split(r"[,\s]+", targets_raw)
    if not isinstance(targets_raw, list):
        targets_raw = []
    fact_gerekceleri = [failing[k] for k in QA_FACT_CHECKS if k in failing]

    def _esle(hedef):
        hedef = str(hedef or "").strip().upper().replace(" ", "_")
        if hedef in QA_EMPTY_TARGETS:
            return set()
        if hedef in QA_REGEN_TARGETS:
            return {hedef}
        if hedef.lower() in QA_CHECK_TARGETS:
            hedef = QA_CHECK_TARGETS[hedef.lower()]
        else:
            hedef = QA_TARGET_ALIASES.get(hedef, hedef)
        if hedef == "FACT":
            return _fact_kapsami(fact_gerekceleri)
        return {hedef} if hedef in QA_REGEN_TARGETS else set()

    hedefler = set()
    taninmayan = []
    for ham in targets_raw:
        eslesen = _esle(ham)
        if not eslesen and str(ham or "").strip().upper() not in QA_EMPTY_TARGETS:
            taninmayan.append(str(ham))
        hedefler |= eslesen

    if overall != "PASS" and not hedefler:
        for check in failing:
            hedefler |= _esle(check)

    # Yalnız length_check yüzünden işaretlenmiş VOICEOVER_FAIL (ve length yok
    # sayıldıysa) düşürülür.
    if qa_state.get("length_check_override"):
        ses_kontrolleri = [k for k in failing if QA_CHECK_TARGETS.get(k) in ("VOICEOVER_FAIL", "FACT")]
        if not ses_kontrolleri:
            hedefler.discard("VOICEOVER_FAIL")
        if not hedefler and not failing and overall == "FAIL":
            # Tek FAIL sebebi yanlış length_check idi.
            overall = "PASS"

    sirali = [h for h in ("VOICEOVER_FAIL", "DUO_SCRIPT_FAIL", "COVER_FAIL", "CAPTION_FAIL", "THREADS_FAIL") if h in hedefler]
    if overall != "PASS" or sirali:
        # Gizlilik kuralı (§10): Actions loguna model METNİ yazılmaz; yalnız FAIL
        # veren kontrol adları. Gerekçeler Telegram raporunda (özel sohbet) ve
        # Script Writer'a verilen QA geri bildiriminde kullanılır.
        log(
            f"🔍 QA sonucu: overall={overall or '?'} | model hedefleri={[str(x)[:40] for x in targets_raw]}"
            f" → yenileme hedefleri={sirali}" + (f" | tanınmayan={[x[:40] for x in taninmayan]}" if taninmayan else "")
        )
        log(f"🔍 QA FAIL kontrolleri: {', '.join(failing) or 'kontrol alanı yok'}")
    if targets_raw and [str(x) for x in targets_raw] != sirali:
        qa_state["regeneration_targets_raw"] = [str(x) for x in targets_raw]
    return overall, sirali, failing


def _qa_geri_bildirimi_olustur(failing_checks, targets, limit=1500):
    satirlar = [f"- {k}: {v.strip()[:300]}" for k, v in (failing_checks or {}).items()]
    if not satirlar:
        satirlar = [f"- QA hedefleri: {', '.join(targets or [])} (ayrıntı verilmedi; fact lock dışı iddia, tekrar ve doğallık sorunlarını gider)"]
    return "\n".join(satirlar)[:limit]


def _nonblocking_qa_mi(kalan, ses_basarili, ses_modu, ses_dosyasi, duo_plan, duo_script, log):
    """Yenileme sonrası kalan QA hedefleri render'ı durdurmalı mı?"""
    if not kalan or not ses_basarili or not ses_dosyasi or not os.path.exists(ses_dosyasi):
        return False
    if not kalan <= (QA_SOCIAL_NONBLOCKING_TARGETS | {"DUO_SCRIPT_FAIL"}):
        return False
    if _beklenen_gercek_mod(duo_plan, duo_script) == "DUO" and ses_modu != "DUO":
        return False
    if "DUO_SCRIPT_FAIL" in kalan and not _duo_qa_nonblocking_mi(ses_basarili, ses_modu, ses_dosyasi, duo_script, log):
        return False
    sosyal = sorted(kalan & QA_SOCIAL_NONBLOCKING_TARGETS)
    if sosyal and callable(log):
        log(f"⚠️ QA yenileme sonrası yalnız videoyu değiştirmeyen katmanları işaretledi ({', '.join(sosyal)}); geçerli TTS bulunduğu için render uyarıyla devam ediyor.")
    return True


def _otv_sosyal_kilitle(reels_state, caption_state, threads_state, fact_state, log):
    """Açıklama, Threads ve kapak metnini ÖTV kilidine çeker. Ses dosyası çoktan
    üretildiyse seslendirme metnine burada dokunulmaz; uyuşmazlık QA yenilemesine kalır."""
    if isinstance(reels_state, dict):
        reels_state["kapak_basliklari"] = kapaklari_otv_kilidine_cek(
            reels_state.get("kapak_basliklari") or [], fact_state, log,
        )
    caption_state = sosyal_metni_otv_kilidine_cek(
        caption_state, ("reels_aciklamasi", "reels_aciklama"), fact_state, log, "aciklama",
    )
    threads_state = sosyal_metni_otv_kilidine_cek(
        threads_state, ("threads_aciklamasi",), fact_state, log, "threads",
    )
    return caption_state, threads_state


def _otv_qa_zorla(qa_state, reels_state, caption_state, threads_state, fact_state, log):
    sorunlar = otv_tutarlilik_sorunlari(reels_state, caption_state, threads_state, fact_state)
    if not sorunlar:
        return qa_state
    if callable(log):
        log("⚠️ ÖTV tutarlılık kilidi: " + "; ".join(sorunlar)[:280])
    qa_state = dict(qa_state or {})
    qa_state["overall"] = "FAIL"
    qa_state["fact_check"] = ("FAIL: " + "; ".join(sorunlar))[:500]
    hedefler = [str(x) for x in (qa_state.get("regeneration_targets") or []) if str(x).strip()]
    metin = " ".join(sorunlar)
    if "seslendirme" in metin or "kanallar" in metin:
        hedefler.append("VOICEOVER_FAIL")
    if "aciklama" in metin or "kapak" in metin or "kanallar" in metin:
        hedefler.append("CAPTION_FAIL")
    if "threads" in metin or "kanallar" in metin:
        hedefler.append("THREADS_FAIL")
    if not any(h in {"VOICEOVER_FAIL", "CAPTION_FAIL", "THREADS_FAIL"} for h in hedefler):
        hedefler.extend(["VOICEOVER_FAIL", "CAPTION_FAIL"])
    qa_state["regeneration_targets"] = hedefler
    return qa_state


def _qa_calistir(router, video_state, fact_state, editorial_state, reels_state, caption_state, threads_state, sure_saniye, log, duo_plan=None, duo_script=None, ton=None):
    content = girdi_birlestir(durumu_metne_donustur('VIDEO', video_state), durumu_metne_donustur('FACT LOCK', fact_state), durumu_metne_donustur('EDITORIAL', editorial_state), durumu_metne_donustur('REELS', reels_state), durumu_metne_donustur('DUO PLAN', duo_plan or {}), durumu_metne_donustur('DUO SCRIPT', duo_script or {}), durumu_metne_donustur('CAPTION', caption_state), durumu_metne_donustur('THREADS', threads_state), f'VIDEO SÜRESİ: {sure_saniye}', f'SEÇİLEN İÇERİK TÜRÜ: {ton or "dengeli"}', vergi_kilidi_talimati(fact_state))
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
    caption_state = _caption_state_normalize(caption_state, log)
    if caption_state.get("reels_aciklamasi") and caption_state.get("reels_hashtagleri"):
        return caption_state, model_caption
    log("⚠️ Metadata ajanı eksik caption/hashtag döndü; Caption ajanı ile tamamlanıyor.")
    try:
        yeni, model = _caption_calistir(router, reels_state, fact_state, editorial_state, video_state, log, ton)
    except Exception as exc:
        log(f"⚠️ Caption tamamlama başarısız; worker sosyal fallback'i devreye girecek: {str(exc)[:160]}")
        return caption_state, model_caption
    yeni = _caption_state_normalize(yeni, log)
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


def _arka_planda(fn, *args, **kwargs):
    """fn'i tek iş parçacıklı bir havuzda başlatır ve Future döndürür.
    Havuz hemen kapatılır (shutdown(wait=False)); iş bitince thread sonlanır."""
    havuz = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline-bg")
    try:
        return havuz.submit(fn, *args, **kwargs)
    finally:
        havuz.shutdown(wait=False)


def _threads_future_sonucu(future, router, video_state, fact_state, editorial_state, log, ton):
    try:
        return future.result()
    except Exception as exc:
        log(f"⚠️ Paralel Threads kolu hata verdi; senkron yeniden deneniyor: {type(exc).__name__}: {str(exc)[:160]}")
        return _threads_calistir(router, video_state, fact_state, editorial_state, log, ton)


def _qa_regeneration_loop(router, video_state, fact_state, editorial_state, reels_state, caption_state, threads_state, duo_plan, duo_script, sure_saniye, ton, legacy_voice, log, voice_initial_instruction='', production_notes='', ses_modu_notlari=None, editorial_hazirlayici=None):
    """Agentic üretim + caption/threads + final QA (maks 1 kontrollü yenileme).

    editorial_hazirlayici: (isteğe bağlı) anlatım modu kararını içeren nihai
      editorial_state'i döndüren callable. Verilirse mod kararı arka planda
      hesaplanırken Detective/Hook çalışır; sonuç yalnız Script Writer'dan önce
      beklenir.

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

    editorial_ilk = editorial_state
    editorial_cache = {}

    def _editorial_son():
        if "v" not in editorial_cache:
            deger = editorial_ilk
            if callable(editorial_hazirlayici):
                try:
                    deger = editorial_hazirlayici() or editorial_ilk
                except Exception as exc:
                    log(f"⚠️ Anlatım modu kararı alınamadı; varsayılan mod kullanılacak: {type(exc).__name__}: {str(exc)[:160]}")
            editorial_cache["v"] = deger
        return editorial_cache["v"]

    def _mod_karari_saglayici():
        return _mod_karari_al(_editorial_son())

    # Threads yalnız video/fact/editorial'a bağlıdır: agentic döngüyle EŞZAMANLI
    # başlar (seri akışta Metadata'dan sonra ~15 sn bekliyordu).
    threads_future = _arka_planda(_threads_calistir, router, video_state, fact_state, editorial_ilk, log, ton)

    # QA yenilemesinde Detective/Hook çıktıları yeniden kullanılır.
    baglam = {}

    # Agentic Üretim Başlatılıyor
    reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, metadata_state = agentic_icerik_uretimi(
        router, video_state, fact_state, editorial_ilk, sure_saniye, ton, legacy_voice, log,
        mod_karari=_mod_karari_saglayici if callable(editorial_hazirlayici) else _mod_karari_al(editorial_ilk),
        baglam=baglam,
    )
    editorial_state = _editorial_son()
    mod_karari = _mod_karari_al(editorial_state)

    # Metadata'dan Caption'ı al
    caption_state = _caption_state_normalize(metadata_state, log)
    model_caption = "agentic"
    caption_state, model_caption = _caption_eksikse_tamamla(router, caption_state, model_caption, reels_state, fact_state, editorial_state, video_state, log, ton)

    # Threads (paralel koldan)
    threads_state, model_threads = _threads_future_sonucu(threads_future, router, video_state, fact_state, editorial_state, log, ton)

    for qa_round in range(MAX_QA_REGEN + SOCIAL_QA_EXTRA_REGEN + 1):
        qa_rounds = qa_round
        caption_state, threads_state = _otv_sosyal_kilitle(reels_state, caption_state, threads_state, fact_state, log)
        qa_state, _ = _qa_calistir(router, video_state, fact_state, editorial_state, reels_state, caption_state, threads_state, sure_saniye, log, duo_plan, duo_script, ton)
        qa_state = _otv_qa_zorla(qa_state, reels_state, caption_state, threads_state, fact_state, log)
        if not isinstance(qa_state, dict):
            qa_state = _object_state_or_empty(qa_state)
        qa_state = dict(qa_state)

        targets_ham = qa_state.get('regeneration_targets') or []
        if isinstance(targets_ham, str):
            targets_ham = re.split(r"[,\s]+", targets_ham)
        targets_ham = {str(x).strip().upper().replace(" ", "_") for x in targets_ham if str(x).strip()} if isinstance(targets_ham, list) else set()
        overall, supported_targets, failing_checks = _qa_sonucunu_coz(qa_state, reels_state, sure_saniye, log, ses_dosyasi)
        qa_state["regeneration_targets"] = list(supported_targets)

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
                if qa_state.get("overall") != "PASS" and not qa_state.get("qa_unavailable"):
                    qa_state["overall_model"] = qa_state.get("overall")
                    qa_state["overall"] = "PASS"
                return reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, threads_state, qa_state, qa_rounds, model_caption, model_threads, True

        if not supported_targets:
            log("❌ QA FAIL döndü ama yenilenebilir hedef çıkarılamadı; render güvenli biçimde durduruluyor.")
            break
        target_set = set(supported_targets)
        if qa_round >= MAX_QA_REGEN:
            sosyal_ek_tur = qa_round < MAX_QA_REGEN + SOCIAL_QA_EXTRA_REGEN and target_set <= QA_SOCIAL_NONBLOCKING_TARGETS
            if not sosyal_ek_tur:
                break
            log(f"♻️ Kalan QA hataları yalnız sosyal katmanda ({', '.join(supported_targets)}); video yeniden üretilmeden ek bir düzeltme turu yapılıyor.")

        voice_needed = bool(target_set & {'VOICEOVER_FAIL', 'DUO_SCRIPT_FAIL'})
        cover_needed = 'COVER_FAIL' in target_set
        downstream_threads = 'THREADS_FAIL' in target_set
        caption_needed = 'CAPTION_FAIL' in target_set
        geri_bildirim = _qa_geri_bildirimi_olustur(failing_checks, supported_targets)
        # Kanca (ilk 3 sn) Hook ajanından gelir ve Script Writer'a girdi olur:
        # QA kancayı ya da gerçekliği (Hook'taki doğrulanmamış iddia senaryoya
        # geri taşınmasın) işaretlediyse Hook da QA geri bildirimiyle yenilenir.
        hook_sorunu = (
            "hook_check" in failing_checks
            or "HOOK_FAIL" in targets_ham
            or any(k in failing_checks for k in QA_FACT_CHECKS)
            or any(QA_TARGET_ALIASES.get(t) == "FACT" for t in targets_ham)
        )

        log(f"⚠️ QA başarısız bulundu ({', '.join(supported_targets)}). İlgili agentic katmanlar QA geri bildirimiyle yeniden üretiliyor...")

        if voice_needed:
            onceki = (reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, model_caption)
            baglam["hook_yenile"] = cover_needed or hook_sorunu
            try:
                reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, metadata_state = agentic_icerik_uretimi(
                    router, video_state, fact_state, editorial_state, sure_saniye, ton, legacy_voice, log,
                    mod_karari=mod_karari, baglam=baglam, qa_geri_bildirimi=geri_bildirim,
                )
                if not ses_basarili and onceki[4]:
                    # Yeni tur ses üretemediyse elde kalan geçerli üretim çöpe atılmaz.
                    log("⚠️ QA yenilemesi geçerli TTS üretemedi; önceki agentic çıktı korunuyor.")
                    reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, model_caption = onceki
                elif not caption_needed:
                    caption_state = _caption_state_normalize(metadata_state)
                    model_caption = "agentic"
                    caption_state, model_caption = _caption_eksikse_tamamla(router, caption_state, model_caption, reels_state, fact_state, editorial_state, video_state, log, ton)
            except Exception as exc:
                # QA yenilemesi API yoğunluğunda düşerse elde kalan geçerli
                # üretim çöpe atılmaz; önceki sürüm korunur.
                log(f"⚠️ QA yenilemesi üretilemedi; önceki agentic çıktı korunuyor: {type(exc).__name__}: {str(exc)[:160]}")
                reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, model_caption = onceki
        elif cover_needed:
            # Kapak başlıkları sese gömülü değil: Script + TTS yeniden üretilmez.
            try:
                reels_state = kapaklari_yeniden_uret(router, reels_state, baglam, fact_state, editorial_state, log, qa_geri_bildirimi=geri_bildirim)
            except Exception as exc:
                log(f"⚠️ Kapak yenilemesi üretilemedi; önceki kapak seti korunuyor: {type(exc).__name__}: {str(exc)[:160]}")

        # Caption QA tarafından işaretlendiyse (seslendirme yenilense de) Caption
        # ajanı QA gerekçeleriyle ve GÜNCEL seslendirmeyle yeniden yazar; Metadata
        # ajanının geri bildirimsiz caption'ı kullanılmaz.
        if caption_needed:
            onceki_caption = (caption_state, model_caption)
            try:
                caption_state, model_caption = _caption_calistir(router, reels_state, fact_state, editorial_state, video_state, log, ton, qa_geri_bildirimi=geri_bildirim)
            except Exception:
                caption_state, model_caption = onceki_caption
            if model_caption == "local-fallback" and onceki_caption[1] not in ("local-fallback", "hata"):
                # Yenileme modeli yanıt vermedi: modelin önceki caption'ı güvenli
                # şablondan iyidir.
                log("⚠️ Caption yenilemesi yanıt vermedi; önceki model caption'ı korunuyor.")
                caption_state, model_caption = onceki_caption
            caption_state = _caption_state_normalize(caption_state, log)

        if downstream_threads:
            onceki_threads = (threads_state, model_threads)
            try:
                threads_state, model_threads = _threads_calistir(router, video_state, fact_state, editorial_state, log, ton, qa_geri_bildirimi=geri_bildirim)
            except Exception:
                threads_state, model_threads = onceki_threads
            if model_threads == "local-fallback" and onceki_threads[1] not in ("local-fallback", "hata"):
                log("⚠️ Threads yenilemesi yanıt vermedi; önceki model Threads metni korunuyor.")
                threads_state, model_threads = onceki_threads
            threads_state = _threads_state_normalize(threads_state)

    # Yenileme bitti ama QA yalnızca videoyu DEĞİŞTİRMEYEN katmanları işaretledi
    # (caption / threads / kapak metni) ya da yalnız DUO scriptini işaretledi ve
    # elde geçerli bir TTS varsa render boğulmaz; sorunlar raporda uyarı olarak
    # kalır. Seslendirme/gerçeklik (VOICEOVER) hataları fail-closed kalır.
    kalan = {str(x).strip().upper() for x in (qa_state.get('regeneration_targets') or []) if str(x).strip()}
    if kalan and _nonblocking_qa_mi(kalan, ses_basarili, ses_modu, ses_dosyasi, duo_plan, duo_script, log):
        qa_state = dict(qa_state)
        qa_state["overall_model"] = qa_state.get("overall")
        qa_state["overall"] = "PASS"
        qa_state["regeneration_targets"] = []
        qa_state["nonblocking_targets"] = sorted(kalan)
        if "DUO_SCRIPT_FAIL" in kalan:
            qa_state["duo_nonblocking_fallback"] = True
        if kalan & QA_SOCIAL_NONBLOCKING_TARGETS:
            qa_state["social_nonblocking_fallback"] = True
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
    mod_future = _anlatim_modu_arka_planda(router, video_state, fact_state, editorial_state, sure_saniye, icerik_tonu, metin_uretim_notlari, log_ekle)
    _ilerleme(ilerlemeyi_guncelle, 4); log_ekle('🎙️ Agentic Viral Üretim Döngüsü Başlatılıyor (4 Ajan)...')
    legacy_voice = secilen_ses_ingilizce if isinstance(secilen_ses_ingilizce, str) and secilen_ses_ingilizce.strip() else 'Autonoe'
    reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, threads_state, qa_state, qa_rounds, model_caption, model_threads, qa_pass = _qa_regeneration_loop(
        router, video_state, fact_state, editorial_state, {}, {}, {}, {}, {}, sure_saniye, icerik_tonu, legacy_voice, log_ekle, production_notes=metin_uretim_notlari,
        editorial_hazirlayici=mod_future.result,
    )
    editorial_state = mod_future.result()
    state['editorial_state'] = editorial_state; state['anlatim_modu_karari'] = _mod_karari_al(editorial_state)
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
    # Metin modda kullanıcı metni = üretim notu; açık ses modu talebi burada aranır.
    mod_future = _anlatim_modu_arka_planda(router, video_state, fact_state, editorial_state, sure_saniye, icerik_tonu, metin, log_ekle)
    _ilerleme(ilerlemeyi_guncelle, 4, '🎙️ Agentic Viral Üretim Döngüsü Başlatılıyor (4 Ajan)...'); legacy_voice = secilen_ses_ingilizce if isinstance(secilen_ses_ingilizce, str) and secilen_ses_ingilizce.strip() else 'Autonoe'
    reels_state, model_reels, duo_plan, duo_script, ses_basarili, kullanilan_ses_modeli, ses_modu, ses_dosyasi, caption_state, threads_state, qa_state, qa_rounds, model_caption, model_threads, qa_pass = _qa_regeneration_loop(
        router, video_state, fact_state, editorial_state, {}, {}, {}, {}, {}, sure_saniye, icerik_tonu, legacy_voice, log_ekle, production_notes=metin, ses_modu_notlari='',
        editorial_hazirlayici=mod_future.result,
    )
    editorial_state = mod_future.result()
    state['editorial_state'] = editorial_state; state['anlatim_modu_karari'] = _mod_karari_al(editorial_state)
    state['reels_state'] = reels_state; state['duo_plan'] = duo_plan; state['duo_script'] = duo_script; state['ses_modu'] = ses_modu; state['qa_regeneration_rounds'] = qa_rounds; state['qa_pass'] = qa_pass
    state['caption_state'] = _caption_state_normalize(caption_state, log_ekle); state['threads_state'] = _threads_state_normalize(threads_state); state['qa_state_final'] = qa_state
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
