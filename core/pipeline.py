"""
pipeline.py — Ultimate Content Engine orkestratörü.
Agentic (4 Ajanlı) Viral İçerik Üretim Mimarisi.
"""
import json
import os, re
import shutil
import time
from core.web_search import web_arastirma_yap
from concurrent.futures import ThreadPoolExecutor
from core.config import KELIME_HIZI_ORANI, SES_HIZ_CARPANI, PIPELINE_ADIMLARI
from core.schemas import (
    VIDEO_ANALYSIS_SCHEMA, FACT_LOCK_SCHEMA, EDITORIAL_SCHEMA, 
    CAPTION_SCHEMA, THREADS_SCHEMA, QA_SCHEMA,
    DETECTIVE_SCHEMA, HOOK_GEN_SCHEMA, SCRIPT_WRITER_SCHEMA, CRITIC_SCHEMA, METADATA_GEN_SCHEMA
)
from core.prompts import (
    forensic_analiz_promptunu_olustur, research_promptunu_olustur, editorial_promptunu_olustur,
    caption_promptunu_olustur, threads_promptunu_olustur,
    qa_promptunu_olustur, durumu_metne_donustur, girdi_birlestir,
    _reels_kelime_ayarlarini_hazirla
)
from core.media import (
    gecici_ses_yolu,
    gecici_dosya_yolu,
    temp_dosya_temizle,
    video_ve_sesi_birlestir,
    _ses_suresini_al,
    medya_raporu,
    video_suresini_al,
)
from core.narration_mode import anlatim_modu_karar_ver, mod_kilit_talimati, mod_kilidini_uygula
from duo.duo_strategy import normalize_duo_strategy
from duo.duo_audio import duo_ses_uret

TOPLAM_ADIM = len(PIPELINE_ADIMLARI)
MAX_QA_REGEN = 1
VOICE_REGEN_MAX = 2
VOICE_DURATION_MIN_RATIO = 0.85
VOICE_DURATION_MAX_RATIO = 1.15

QA_REGEN_TARGETS = {
    "VOICEOVER_FAIL",
    "COVER_FAIL",
    "DUO_SCRIPT_FAIL",
    "CAPTION_FAIL",
    "THREADS_FAIL",
}


def _safe_result_summary(value):
    """Log model çıktısının içeriğini değil yalnızca güvenli yapısal özetini."""
    state = value[0] if isinstance(value, tuple) and value else value
    model = value[1] if isinstance(value, tuple) and len(value) > 1 else None
    if isinstance(state, dict):
        keys = sorted(str(k) for k in state.keys())
        try:
            json_chars = len(json.dumps(state, ensure_ascii=False, default=str))
        except Exception:
            json_chars = 0
        list_items = sum(len(v) for v in state.values() if isinstance(v, list))
        text_chars = sum(len(v) for v in state.values() if isinstance(v, str))
        detail = (
            f"dict keys={keys[:16]}" + (f" (+{len(keys)-16})" if len(keys) > 16 else "")
            + f" | json_chars={json_chars} text_chars={text_chars} list_items={list_items}"
        )
    elif isinstance(state, list):
        detail = f"list items={len(state)}"
    elif isinstance(state, str):
        detail = f"text chars={len(state)}"
    else:
        detail = type(state).__name__
    return detail + (f" | model={model}" if model else "")


def _run_timed(log, label, callback):
    """Actions loguna başlangıç/bitiş/hata süresi yazan ortak ölçüm katmanı."""
    started = time.perf_counter()
    log(f"⏱️ START | {label}")
    try:
        result = callback()
    except Exception as exc:
        elapsed = time.perf_counter() - started
        log(f"⏱️ FAIL  | {label} | {elapsed:.2f}s ({elapsed/60:.2f} dk) | {type(exc).__name__}: {str(exc)[:180]}")
        raise
    elapsed = time.perf_counter() - started
    log(f"⏱️ END   | {label} | {elapsed:.2f}s ({elapsed/60:.2f} dk) | {_safe_result_summary(result)}")
    return result


def _coerce_positive_float(value):
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _resolve_video_duration_strict(sure_saniye, temp_input_video):
    direct = _coerce_positive_float(sure_saniye)
    if direct is not None:
        return direct
    if temp_input_video and os.path.exists(temp_input_video):
        measured = _coerce_positive_float(video_suresini_al(temp_input_video))
        if measured is not None:
            return measured
    return None


def _qa_regeneration_targets(qa_state):
    if not isinstance(qa_state, dict):
        return []
    raw = qa_state.get("regeneration_targets") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(x).strip().upper() for x in raw if str(x).strip().upper() in QA_REGEN_TARGETS]


def _qa_is_clean_pass(qa_state):
    if not isinstance(qa_state, dict):
        return False
    overall = str(qa_state.get("overall") or qa_state.get("status") or "").strip().upper()
    return overall == "PASS" and not _qa_regeneration_targets(qa_state)


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
        'selected_hook': _secilen_hook_getir(reels_state),
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


def _caption_state_normalize(value):
    parsed = _json_object_or_none(value)
    if parsed is not None:
        return {
            "reels_aciklamasi": str(parsed.get("reels_aciklamasi", "") or ""),
            "reels_hashtagleri": parsed.get("reels_hashtagleri") if isinstance(parsed.get("reels_hashtagleri"), list) else [],
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


def _object_state_or_empty(value):
    parsed = _json_object_or_none(value)
    return parsed if parsed is not None else {}


def _ilerleme(cb, n, msg=None):
    if cb:
        cb(n, TOPLAM_ADIM, msg or PIPELINE_ADIMLARI[n-1])


def _secilen_hook_getir(reels_state):
    reels_state = _object_state_or_empty(reels_state)
    families = reels_state.get('hook_families') or []
    if not families:
        return {"kapak_metni": reels_state.get("kapak_basliklari", [{}])[0].get("ana", "")}
    return families[0] if families else {}


def _forensic_analiz_calistir(router, video_bytes, mime_type, analiz_notlari, sure_saniye, log):
    ek = ''
    if analiz_notlari and analiz_notlari.strip():
        ek = f"\nÖNEMLİ VİDEO ANALİZ NOTLARI:\n{analiz_notlari.strip()}\n"
    return _run_timed(
        log, "Forensic video analizi (Gemini)",
        lambda: router.video_analiz_et(video_bytes,mime_type,forensic_analiz_promptunu_olustur(ek,sure_saniye),VIDEO_ANALYSIS_SCHEMA,log),
    )


def _research_calistir(router, video_state, log):
    video_state = _object_state_or_empty(video_state)
    web_sonuclari = web_arastirma_yap(video_state, log)
    content = girdi_birlestir(
        durumu_metne_donustur('VIDEO IDENTITY',video_state.get('video_identity',{})),
        durumu_metne_donustur('OBSERVED FACTS',video_state.get('observed_facts',[])),
        durumu_metne_donustur('UNKNOWNS',video_state.get('unknowns',[])),
        durumu_metne_donustur('POSSIBLE INFERENCE',video_state.get('possible_inference',[])),
        durumu_metne_donustur('ARAŞTIRMA İHTİYAÇLARI',video_state.get('viral_arastirma_ihtiyaclari',[])),
        durumu_metne_donustur('WEB ARAŞTIRMA SONUÇLARI', web_sonuclari or "Web araştırması yapılamadı.")
    )
    return _run_timed(
        log, "Research / Fact Lock (Agentic Web + Gemini Analiz)",
        lambda: router.metin_uret(content,research_promptunu_olustur(),FACT_LOCK_SCHEMA,log,arama_kullan=False),
    )


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
    content = girdi_birlestir(durumu_metne_donustur('VIDEO STATE',video_state),durumu_metne_donustur('FACT LOCK',fact_state),notes or '')
    result, model = _run_timed(
        log, "Editorial Brain (Gemini)",
        lambda: router.metin_uret(content,editorial_promptunu_olustur(ton),EDITORIAL_SCHEMA,log,arama_kullan=False),
    )
    return _editorial_oncelik_denetimi(result, log), model


def _caption_calistir(router,reels_state,fact_state,editorial_state,video_state,log,ton=None):
    content = girdi_birlestir(durumu_metne_donustur('REELS',reels_state),durumu_metne_donustur('FACT LOCK',fact_state),durumu_metne_donustur('EDITORIAL',editorial_state),durumu_metne_donustur('VIDEO',video_state))
    result, model = _run_timed(
        log, "Caption + Hashtag (Gemini)",
        lambda: router.metin_uret(content,caption_promptunu_olustur(ton),CAPTION_SCHEMA,log,arama_kullan=False),
    )
    return _caption_state_normalize(result), model


def _threads_calistir(router,video_state,fact_state,editorial_state,log,ton=None):
    content = girdi_birlestir(durumu_metne_donustur('VIDEO',video_state),durumu_metne_donustur('FACT LOCK',fact_state),durumu_metne_donustur('EDITORIAL',editorial_state))
    result, model = _run_timed(
        log, "Threads (Gemini)",
        lambda: router.metin_uret(content,threads_promptunu_olustur(ton),THREADS_SCHEMA,log,arama_kullan=False),
    )
    return _threads_state_normalize(result), model


def _kelime_sayisi(metin):
    return len(re.findall(r"\b[\wÇĞİÖŞÜçğıöşüÀ-ÿ]+(?:[-'][\wÇĞİÖŞÜçğıöşüÀ-ÿ]+)*\b", str(metin or ""), re.UNICODE))


def _explicit_voice_mode_from_notes(notes):
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
    karar = _run_timed(
        log, "Anlatım modu kararı (Gemini)",
        lambda: anlatim_modu_karar_ver(router, video_state, fact_state, editorial, sure_saniye, ton, log),
    )
    if karar:
        editorial['anlatim_modu_karari'] = karar
    return editorial


def _mod_icin_legacy_ses(mode, default_voice='Autonoe'):
    if mode == 'SOLO_FEMALE':
        return 'Autonoe'
    if mode == 'SOLO_MALE':
        return 'Charon'
    return default_voice or 'Autonoe'


def _beklenen_gercek_mod(duo_plan, duo_script):
    mode = str(((duo_script or {}).get("contract", {}) or {}).get("mode") or (duo_plan or {}).get("mode") or "DUO").strip().upper()
    return mode if mode in {"DUO", "SOLO_FEMALE", "SOLO_MALE"} else "DUO"


def _duo_ses_veya_legacy_uret(router, duo_script, legacy_text, legacy_voice, log, output_path):
    mode = _beklenen_gercek_mod({}, duo_script)
    effective_legacy_voice = _mod_icin_legacy_ses(mode, legacy_voice)

    if mode == "DUO":
        if not (duo_script and duo_script.get('status') == 'ready' and duo_script.get('segments')):
            log("❌ DUO mode aktif ama doğrulanmış duo_script yok; legacy fallback engellendi.")
            return False, None, "DUO"
        ok, info = _run_timed(
            log, "DUO multi-speaker TTS + WAV hazırlama",
            lambda: duo_ses_uret(router, duo_script['segments'], output_path, log, hiz_carpani=SES_HIZ_CARPANI),
        )
        if ok and os.path.exists(output_path):
            return True, info, "DUO"
        log("❌ DUO TTS üretilemedi; DUO modunda legacy fallback kapalı.")
        return False, None, "DUO"

    ok, info = _run_timed(
        log, "Legacy tek ses TTS + WAV hazırlama",
        lambda: router.ses_uret(legacy_text, effective_legacy_voice, output_path, log, hiz_carpani=SES_HIZ_CARPANI),
    )
    return ok, info, mode


def _ses_sure_uyumlu_mu(ses_dosyasi, video_suresi):
    ses_suresi = _ses_suresini_al(ses_dosyasi) if ses_dosyasi and os.path.exists(ses_dosyasi) else 0.0
    video_suresi = _coerce_positive_float(video_suresi)
    if video_suresi is None or ses_suresi <= 0:
        return False, ses_suresi, 0.0
    oran = ses_suresi / video_suresi
    return VOICE_DURATION_MIN_RATIO <= oran <= VOICE_DURATION_MAX_RATIO, ses_suresi, oran


# ==========================================
# AGENTIC 4 AJANLI SİSTEM (DETECTIVE -> HOOK -> SCRIPT -> CRITIC -> METADATA)
# ==========================================

def _detective_calistir(router, video_state, fact_state, editorial_state, log):
    prompt = (
        "Sen otoXtra'nın Dedektif Ajanısın. Görevin: İzleyicinin sinir uçlarına dokunacak kronik şikayetleri, "
        "Türkiye'ye özel vergi/maliyet mağduriyetlerini ve en kışkırtıcı ham bilgiyi bulmak.\n"
        "Aşağıdaki video, fact lock ve editorial brief'e dayanarak sadece JSON şemasına uygun çıktı ver."
    )
    content = girdi_birlestir(
        durumu_metne_donustur('VIDEO', video_state),
        durumu_metne_donustur('FACT LOCK', fact_state),
        durumu_metne_donustur('EDITORIAL', editorial_state)
    )
    return _run_timed(
        log, "🕵️ Detective Ajan (Derin Analiz)",
        lambda: router.metin_uret(content, prompt, DETECTIVE_SCHEMA, log, arama_kullan=False),
    )

def _hook_gen_calistir(router, detective_state, fact_state, editorial_state, log):
    prompt = (
        "Sen otoXtra'nın Kışkırtıcı Kanca Üreticisisin. Dedektifin bulduğu malzemeyi kullanarak "
        "ilk 3 saniyede izleyiciyi şok edecek bir kanca üreteceksin.\n"
        "Şablonlardan birini seç: Efsane_Curutme, Ters_Kose, Negatif_Uyari.\n"
        "Kapak metni ve ilk 3 saniye kancası ultra viral ve iddialı olmalı."
    )
    content = girdi_birlestir(
        durumu_metne_donustur('DETECTIVE', detective_state),
        durumu_metne_donustur('FACT LOCK', fact_state),
        durumu_metne_donustur('EDITORIAL', editorial_state)
    )
    return _run_timed(
        log, "🪝 Hook Generator Ajan (Kanca Üretimi)",
        lambda: router.metin_uret(content, prompt, HOOK_GEN_SCHEMA, log, arama_kullan=False),
    )

def _script_writer_calistir(router, hook_state, detective_state, fact_state, editorial_state, video_state, sure_saniye, log, feedback="", hedef_kelime_bilgisi="", mod="DUO"):
    if mod == "DUO":
        karakter_bilgisi = "Karakterler: Autonoe (şüpheci, zeki kadın), Charon (iddialı, kanıtlayan erkek). İki kişilik doğal muhabbet."
    elif mod == "SOLO_FEMALE":
        karakter_bilgisi = "Karakter: Sadece Autonoe (şüpheci, zeki kadın). Tek kişilik monolog."
    else:
        karakter_bilgisi = "Karakter: Sadece Charon (iddialı, kanıtlayan erkek). Tek kişilik monolog."

    prompt = (
        f"Sen otoXtra'nın Sohbet Yazarısın. {karakter_bilgisi}\n"
        "Kanca ve Dedektif verilerini kullanarak doğal bir anlatım yaz.\n"
        "TTS ETİKETLERİ: Her repliğin başına veya içine duygu etiketi ekle. Örn: [gülerek], [şaşırarak], [vurgulu], ... (duraksama).\n"
        "FİNAL: Senaryoyu kesin bir kararla bitirme. Son cümle, izleyicileri ikiye bölecek ve yorumlarda tartışmaya itecek kışkırtıcı bir SORU olmalı.\n"
        "Hedef kelime sayısı ve süre: video süresine uygun doğal konuşma hızı."
    )
    if hedef_kelime_bilgisi:
        prompt += f"\n\n🚨 KELİME LİMİTİ: {hedef_kelime_bilgisi}"
    if feedback:
        prompt += f"\n\n🚨 ELEŞTİRMEN GERİ BİLDİRİMİ (Revize Et): {feedback}"
        
    content = girdi_birlestir(
        durumu_metne_donustur('HOOK', hook_state),
        durumu_metne_donustur('DETECTIVE', detective_state),
        durumu_metne_donustur('FACT LOCK', fact_state),
        durumu_metne_donustur('EDITORIAL', editorial_state),
        durumu_metne_donustur('VIDEO', video_state),
        f"VIDEO SÜRESİ: {sure_saniye} saniye"
    )
    return _run_timed(
        log, "📝 Script Writer Ajan (Senaryo Yazımı)",
        lambda: router.metin_uret(content, prompt, SCRIPT_WRITER_SCHEMA, log, arama_kullan=False),
    )

def _critic_calistir(router, script_state, hook_state, log):
    prompt = (
        "Sen trol, şüpheci ve zor beğenen bir Türk izleyicisisin. Bu senaryoyu oku.\n"
        "Robotik mi? Sıkıcı mı? Sonundaki soru yorum yaptıracak kadar kışkırtıcı mı?\n"
        "Eğer 10 üzerinden 7 veya üzeriyse approved: true yap.\n"
        "Değilse approved: false yap ve SADECE NET BİR REVİZE TALİMATI (feedback) ver. Uzatma."
    )
    content = girdi_birlestir(
        durumu_metne_donustur('HOOK', hook_state),
        durumu_metne_donustur('SCRIPT', script_state)
    )
    return _run_timed(
        log, "🔥 Critic Ajan (Trol İzleyici Eleştirisi)",
        lambda: router.metin_uret(content, prompt, CRITIC_SCHEMA, log, arama_kullan=False),
    )

def _metadata_gen_calistir(router, script_state, hook_state, fact_state, log):
    prompt = (
        "Sen otoXtra'nın Viral Metadata Ajanısın. En tartışmalı cümleyi cımbızla ve Instagram/TikTok algoritmasını besleyecek "
        "başlık, açıklama ve hashtag'leri üret. Yorum ve kaydetme odaklı ol."
    )
    content = girdi_birlestir(
        durumu_metne_donustur('HOOK', hook_state),
        durumu_metne_donustur('SCRIPT', script_state),
        durumu_metne_donustur('FACT LOCK', fact_state)
    )
    return _run_timed(
        log, "🏷️ Metadata Generator Ajan (Başlık & Caption)",
        lambda: router.metin_uret(content, prompt, METADATA_GEN_SCHEMA, log, arama_kullan=False),
    )


def agentic_icerik_uretimi(router, video_state, fact_state, editorial_state, sure_saniye, ton, legacy_voice, log, mod_karari=None):
    """4 Ajanlı Viral Üretim Döngüsü (Kelime ve TTS Süre Güvenlik Duvarlı)"""
    
    mod = str((mod_karari or {}).get("mode") or "DUO").upper()
    hedef, minimum, maksimum, _, _ = _reels_kelime_ayarlarini_hazirla(sure_saniye, KELIME_HIZI_ORANI)
    hedef_kelime_bilgisi = f"Hedef {hedef} kelime. Kesin aralık {minimum}-{maksimum} kelime."
    
    # 1. Detective (Sadece ilk denemede çalışır, veri değişmez)
    detective_state, _ = _detective_calistir(router, video_state, fact_state, editorial_state, log)
    detective_state = _object_state_or_empty(detective_state)
    
    # 2. Hook Gen (Sadece ilk denemede çalışır)
    hook_state, _ = _hook_gen_calistir(router, detective_state, fact_state, editorial_state, log)
    hook_state = _object_state_or_empty(hook_state)
    
    son_reels = {}
    son_duo_plan = {}
    son_duo_script = {}
    son_metadata = {}
    son_model = 'hata'
    
    # TTS ve Kelime Güvenlik Döngüsü
    for deneme in range(VOICE_REGEN_MAX + 1):
        
        # 3. Script Writer
        script_state, model_script = _script_writer_calistir(
            router, hook_state, detective_state, fact_state, editorial_state, 
            video_state, sure_saniye, log, hedef_kelime_bilgisi=hedef_kelime_bilgisi, mod=mod
        )
        script_state = _object_state_or_empty(script_state)
        son_model = model_script
        
        # 4. Critic
        critic_state, _ = _critic_calistir(router, script_state, hook_state, log)
        critic_state = _object_state_or_empty(critic_state)
        
        # Critic Döngüsü (MAX 1 Revize)
        if not critic_state.get("approved", False) and critic_state.get("score", 10) < 7:
            feedback = critic_state.get("feedback", "Daha doğal ve kışkırtıcı yap.")
            log(f"⚠️ Critic onaylamadı (Score: {critic_state.get('score')}). Revize başlatılıyor...")
            script_state, model_script = _script_writer_calistir(
                router, hook_state, detective_state, fact_state, editorial_state, 
                video_state, sure_saniye, log, feedback=feedback, hedef_kelime_bilgisi=hedef_kelime_bilgisi, mod=mod
            )
            script_state = _object_state_or_empty(script_state)
            son_model = model_script
            
        # Reels State Emülasyonu
        full_text = " ".join([seg.get("text", "") for seg in script_state.get("segments", [])]) + " " + script_state.get("yorum_tetikleyici_soru", "")
        reels_state = {
            "seslendirme_metni": full_text,
            "kapak_basliklari": [{"ana": hook_state.get("kapak_metni", ""), "alt": ""}],
            "hook_families": [{"kapak_ana": hook_state.get("kapak_metni", ""), "ilk_uc_saniye": hook_state.get("ilk_3_saniye_kanca", "")}],
            "metadata": {}
        }
        
        # TTS Segmentlerini Hazırlama (Duygu Etiketli)
        segments = script_state.get("segments", [])
        tts_segments = []
        for seg in segments:
            tag = seg.get('tts_tag', '').strip()
            text = seg.get('text', '').strip()
            tts_text = f"{tag} {text}".strip() if tag else text
            
            if mod == "SOLO_FEMALE":
                tts_segments.append({"speaker": "female", "text": tts_text})
            elif mod == "SOLO_MALE":
                tts_segments.append({"speaker": "male", "text": tts_text})
            else:
                tts_segments.append({"speaker": seg.get("speaker", "female"), "text": tts_text})
            
        if script_state.get("yorum_tetikleyici_soru"):
            last_speaker = segments[-1].get("speaker", "female") if segments else "female"
            final_speaker = "male" if last_speaker == "female" else "female"
            
            if mod == "SOLO_FEMALE":
                final_speaker = "female"
            elif mod == "SOLO_MALE":
                final_speaker = "male"
                
            tts_segments.append({"speaker": final_speaker, "text": f"[vurgulu] {script_state.get('yorum_tetikleyici_soru')}"})

        duo_script = {
            "status": "ready",
            "segments": tts_segments,
            "contract": {"mode": mod},
            "conversation_design": {},
            "model": "agentic"
        }
        
        duo_plan = {"mode": mod, "target_words": hedef}
        
        # Kelime Kontrolü
        adet = _kelime_sayisi(full_text)
        log(f'📝 Agentic Seslendirme uzunluk kontrolü: {adet} kelime | hedef {hedef} | izin verilen {minimum}-{maksimum}')
        
        if adet < minimum or adet > maksimum:
            if deneme < VOICE_REGEN_MAX:
                hedef_kelime_bilgisi = f"Önceki üretim {adet} kelimeydi. Hedef {hedef}, izin verilen {minimum}-{maksimum}. Bu kez metni mutlaka bu aralıkta tut."
                log(f'⚠️ Kelime aralığı dışında; Script Writer yeniden yazıyor ({deneme+1}/{VOICE_REGEN_MAX}).')
                continue
            else:
                log(f'⚠️ Kelime aralığı hala düzeltilemedi, devam ediliyor.')
        
        # TTS Üretimi
        ses_dosyasi = gecici_ses_yolu()
        ok, info, mod_tts = _duo_ses_veya_legacy_uret(router, duo_script, full_text, legacy_voice, log, ses_dosyasi)
        
        if not ok:
            temp_dosya_temizle(ses_dosyasi)
            if deneme < VOICE_REGEN_MAX:
                hedef_kelime_bilgisi = "TTS üretimi başarısız oldu. Daha doğal ve okunabilir bir metin yaz."
                continue
            return reels_state, "hata", duo_plan, duo_script, False, None, "LEGACY", "", {}
            
        uyumlu, ses_suresi, oran = _ses_sure_uyumlu_mu(ses_dosyasi, sure_saniye)
        log(f'🎚️ Agentic TTS gerçek süre kontrolü: video {sure_saniye:.2f}s → ses {ses_suresi:.2f}s | oran {oran:.2f}x')
        
        if os.path.exists(ses_dosyasi) and ses_suresi > 0:
            if not uyumlu:
                if deneme < VOICE_REGEN_MAX:
                    hedef_kelime_bilgisi = f"TTS önceki metni {ses_suresi:.2f} saniye üretti; hedef video {sure_saniye:.2f} saniye. Kelime sayısını {minimum}-{maksimum} aralığına çek."
                    temp_dosya_temizle(ses_dosyasi)
                    continue
                else:
                    log(f'🎚️ TTS/video oranı {oran:.2f}x; video senkron katmanına bırakılıyor.')
            
            # Başarılı TTS ve Süre. Metadata üretilir ve döngüden çıkılır.
            metadata_state, _ = _metadata_gen_calistir(router, script_state, hook_state, fact_state, log)
            metadata_state = _object_state_or_empty(metadata_state)
            reels_state["metadata"] = metadata_state
            
            return reels_state, "agentic", duo_plan, duo_script, True, info, mod_tts, ses_dosyasi, metadata_state

        temp_dosya_temizle(ses_dosyasi)

    return son_reels, son_model, son_duo_plan, son_duo_script, False, None, "LEGACY", "", {}


def _qa_calistir(router,video_state,fact_state,editorial_state,reels_state,caption_state,threads_state,sure_saniye,log,duo_plan=None,duo_script=None,ton=None):
    content=girdi_birlestir(durumu_metne_donustur('VIDEO',video_state),durumu_metne_donustur('FACT LOCK',fact_state),durumu_metne_donustur('EDITORIAL',editorial_state),durumu_metne_donustur('REELS',reels_state),durumu_metne_donustur('DUO PLAN',duo_plan or {}),durumu_metne_donustur('DUO SCRIPT',duo_script or {}),durumu_metne_donustur('CAPTION',caption_state),durumu_metne_donustur('THREADS',threads_state),f'VIDEO SÜRESİ: {sure_saniye}',f'SEÇİLEN İÇERİK TÜRÜ: {ton or "dengeli"}')
    result, model = _run_timed(
        log, "Final QA (Gemini)",
        lambda: router.metin_uret(content,qa_promptunu_olustur(ton),QA_SCHEMA,log,arama_kullan=False),
    )
    result = _object_state_or_empty(result)
    if not result:
        result = {"overall":"FAIL","regeneration_targets":["QA_PARSE_FAIL"]}
    return result, model


def _qa_regeneration_loop(router,video_state,fact_state,editorial_state,reels_state,caption_state,threads_state,duo_plan,duo_script,sure_saniye,ton,legacy_voice,log,voice_initial_instruction='',production_notes='',ses_modu_notlari=None):
    qa_state={}; qa_rounds=0; ses_basarili=False; kullanilan_ses_modeli=None; ses_modu='LEGACY'; ses_dosyasi=''
    
    # Anlatım Modu Kararını Al
    mod_karari = _mod_karari_al(editorial_state)
    
    # Agentic Üretim Başlatılıyor
    reels_state,model_reels,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi, metadata_state = agentic_icerik_uretimi(
        router,video_state,fact_state,editorial_state,sure_saniye,ton,legacy_voice,log, mod_karari=mod_karari
    )
    
    # Metadata'dan Caption'ı al
    caption_state = _caption_state_normalize(metadata_state)
    
    # Threads için eski ajanı kullan
    threads_state, model_threads = _threads_calistir(router, video_state, fact_state, editorial_state, log, ton)

    for qa_round in range(MAX_QA_REGEN+1):
        qa_rounds=qa_round
        qa_state,_=_qa_calistir(router,video_state,fact_state,editorial_state,reels_state,caption_state,threads_state,sure_saniye,log,duo_plan,duo_script,ton)
        if not isinstance(qa_state, dict):
            qa_state = _object_state_or_empty(qa_state)

        targets_raw = qa_state.get('regeneration_targets') or []
        if isinstance(targets_raw, str):
            targets_raw = [targets_raw]
        if not isinstance(targets_raw, list):
            targets_raw = []
        targets=[str(x).strip().upper() for x in targets_raw if str(x).strip()]
        supported_targets=[x for x in targets if x in QA_REGEN_TARGETS]
        overall=str(qa_state.get('overall') or qa_state.get('status') or '').strip().upper()

        expected_mode = _beklenen_gercek_mod(duo_plan, duo_script)
        if overall=='PASS' and not supported_targets:
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
                return reels_state,caption_state,threads_state,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi,qa_state,qa_rounds,model_reels,"agentic",model_threads,True

        if not supported_targets:
            break
        if qa_round >= MAX_QA_REGEN:
            break

        target_set=set(supported_targets)
        creative_needed=bool(target_set & {'VOICEOVER_FAIL','COVER_FAIL', 'DUO_SCRIPT_FAIL'})
        downstream_threads='THREADS_FAIL' in target_set
        
        log(f"⚠️ QA başarısız bulundu. Agentic sistem baştan başlatılıyor...")
        
        if creative_needed:
            reels_state,model_reels,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi, metadata_state = agentic_icerik_uretimi(
                router,video_state,fact_state,editorial_state,sure_saniye,ton,legacy_voice,log, mod_karari=mod_karari
            )
            caption_state = _caption_state_normalize(metadata_state)

        if downstream_threads:
            try:
                threads_state,model_threads=_threads_calistir(router,video_state,fact_state,editorial_state,log,ton)
            except Exception:
                threads_state={"threads_aciklamasi":""}
            threads_state = _threads_state_normalize(threads_state)

    return reels_state,caption_state,threads_state,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi,qa_state,qa_rounds,model_reels,"agentic",model_threads,False


def pipeline_calistir(router,video_bytes,mime_type,temp_input_video,video_analiz_notlari,metin_uretim_notlari,sure_saniye,icerik_tonu,secilen_ses_ingilizce,log_ekle,ilerlemeyi_guncelle=None):
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

    state={'content_tone': str(icerik_tonu or 'dengeli').strip().lower()}
    log_ekle(f"🎯 İçerik türü runtime kilidi aktif: {state['content_tone']}")
    _ilerleme(ilerlemeyi_guncelle,1); log_ekle('🎥 Video analiz ediliyor (Forensic)...')
    video_state,_=_forensic_analiz_calistir(router,video_bytes,mime_type,video_analiz_notlari,sure_saniye,log_ekle); state['video_state']=video_state
    _ilerleme(ilerlemeyi_guncelle,2); log_ekle('🔎 Gerçekler doğrulanıyor (Research / Fact Lock)...')
    fact_state,_=_research_calistir(router,video_state,log_ekle); state['fact_state']=fact_state
    _ilerleme(ilerlemeyi_guncelle,3); log_ekle('🧠 Hikâye seçiliyor (Editorial Brain)...')
    editorial_state,_=_editorial_calistir(router,video_state,fact_state,metin_uretim_notlari,log_ekle,icerik_tonu)
    log_ekle('🎚️ Anlatım modu belirleniyor (tek ses / çift ses)...')
    editorial_state=_anlatim_modu_karari_ekle(router,video_state,fact_state,editorial_state,sure_saniye,icerik_tonu,metin_uretim_notlari,log_ekle)
    state['editorial_state']=editorial_state; state['anlatim_modu_karari']=_mod_karari_al(editorial_state)
    _ilerleme(ilerlemeyi_guncelle,4); log_ekle('🎙️ Agentic Viral Üretim Döngüsü Başlatılıyor (4 Ajan)...')
    legacy_voice = secilen_ses_ingilizce if isinstance(secilen_ses_ingilizce, str) and secilen_ses_ingilizce.strip() else 'Autonoe'
    reels_state,model_reels,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi,caption_state,threads_state,qa_state,qa_rounds,model_caption,model_threads,qa_pass=_qa_regeneration_loop(
        router,video_state,fact_state,editorial_state,{}, {},{}, {},{},sure_saniye,icerik_tonu,legacy_voice,log_ekle, production_notes=metin_uretim_notlari
    )
    state['reels_state']=reels_state; state['duo_plan']=duo_plan; state['duo_script']=duo_script; state['ses_modu']=ses_modu; state['qa_regeneration_rounds']=qa_rounds; state['qa_pass']=qa_pass
    if ses_basarili and ses_dosyasi and os.path.exists(ses_dosyasi):
        stable_tts = gecici_dosya_yolu('pipeline_tts_stable','wav')
        try:
            shutil.copy2(ses_dosyasi, stable_tts)
            ses_dosyasi = stable_tts
            state['ses_dosyasi_son']=ses_dosyasi
            log_ekle('🔒 TTS dosyası pipeline sonuna kadar korunmak üzere sabitlendi.')
        except Exception as exc:
            log_ekle(f'⚠️ TTS sabitleme başarısız; mevcut dosya kullanılmaya devam edilecek: {str(exc)[:150]}')

    _ilerleme(ilerlemeyi_guncelle,5); state['caption_state']=caption_state
    _ilerleme(ilerlemeyi_guncelle,6); state['threads_state']=threads_state
    _ilerleme(ilerlemeyi_guncelle,7); state['qa_state_final']=qa_state
    if not qa_pass:
        _ilerleme(ilerlemeyi_guncelle,8); log_ekle('❌ QA PASS alınamadı; TTS/render aşaması güvenli biçimde durduruldu.')
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

    _ilerleme(ilerlemeyi_guncelle,8); log_ekle(f'🎧 Hazır ses kullanılıyor ({ses_modu} → {_ses_modu_sesi(ses_modu)}).')
    _ilerleme(ilerlemeyi_guncelle,9); log_ekle('🎬 Videoya AI sesi ekleniyor (FFmpeg)...')
    output=gecici_dosya_yolu('output','mp4')
    render_ok = ses_basarili and _run_timed(
        log_ekle, "FFmpeg video + TTS render",
        lambda: video_ve_sesi_birlestir(temp_input_video, ses_dosyasi, output, log_ekle),
    )
    final=output if render_ok and os.path.exists(output) else ''
    input_media=medya_raporu(temp_input_video,'INPUT FINAL',log_ekle) if os.path.exists(temp_input_video) else {}
    output_media=medya_raporu(final,'OUTPUT FINAL',log_ekle) if final else {}
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
    metin=(metin or '').strip()
    if not metin:
        raise ValueError('Metin girdisi boş.')

    sure_saniye = _coerce_positive_float(sure_saniye)
    if sure_saniye is None:
        raise ValueError("Metin modu için geçerli bir sure_saniye gerekli (pozitif sayı).")

    state={'content_tone': str(icerik_tonu or 'dengeli').strip().lower()}
    log_ekle(f"🎯 İçerik türü runtime kilidi aktif: {state['content_tone']}")
    video_state={'video_identity':{'brand':'UNKNOWN','exact_model':'UNKNOWN','confidence':'unknown','source':'telegram_text'},'observed_facts':[metin],'unknowns':[],'possible_inference':[],'viral_arastirma_ihtiyaclari':['Metindeki araç/konu kimliğini ve güncel iddiaları doğrula.'],'visual_opportunities':['Metin tabanlı üretim; video görsel zaman çizelgesi yok.'],'timeline':[]}
    state['video_state']=video_state
    _ilerleme(ilerlemeyi_guncelle,1,'📝 Metin girdisi'); log_ekle('📝 Metin girdisi işleniyor (video analizi atlanıyor)...')
    _ilerleme(ilerlemeyi_guncelle,2,'🔎 Research / Fact Lock'); fact_state,_=_research_calistir(router,video_state,log_ekle); state['fact_state']=fact_state
    _ilerleme(ilerlemeyi_guncelle,3,'🧠 Editorial Brain'); editorial_state,_=_editorial_calistir(router,video_state,fact_state,metin,log_ekle,icerik_tonu)
    log_ekle('🎚️ Anlatım modu belirleniyor (tek ses / çift ses)...')
    editorial_state=_anlatim_modu_karari_ekle(router,video_state,fact_state,editorial_state,sure_saniye,icerik_tonu,'',log_ekle)
    state['editorial_state']=editorial_state; state['anlatim_modu_karari']=_mod_karari_al(editorial_state)
    _ilerleme(ilerlemeyi_guncelle,4,'🎙️ Agentic Viral Üretim Döngüsü Başlatılıyor (4 Ajan)...'); legacy_voice = secilen_ses_ingilizce if isinstance(secilen_ses_ingilizce,str) and secilen_ses_ingilizce.strip() else 'Autonoe'
    reels_state,model_reels,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi,caption_state,threads_state,qa_state,qa_rounds,model_caption,model_threads,qa_pass=_qa_regeneration_loop(
        router,video_state,fact_state,editorial_state,{}, {},{}, {},{},sure_saniye,icerik_tonu,legacy_voice,log_ekle, production_notes=metin, ses_modu_notlari=''
    )
    state['reels_state']=reels_state; state['duo_plan']=duo_plan; state['duo_script']=duo_script; state['ses_modu']=ses_modu; state['qa_regeneration_rounds']=qa_rounds; state['qa_pass']=qa_pass
    state['caption_state']=_caption_state_normalize(caption_state); state['threads_state']=_threads_state_normalize(threads_state); state['qa_state_final']=qa_state
    _ilerleme(ilerlemeyi_guncelle,5,'📝 Caption + hashtag'); _ilerleme(ilerlemeyi_guncelle,6,'🧵 Threads'); _ilerleme(ilerlemeyi_guncelle,7,'🔍 QA')
    _ilerleme(ilerlemeyi_guncelle,8,'🎧 Ses üretiliyor...')
    if not qa_pass:
        log_ekle('❌ QA PASS alınamadı; text-only ses gönderimi durduruldu.')
        ses_basarili=False; ses_dosyasi=''
    elif ses_basarili:
        log_ekle(f'🎧 Hazır ses kullanılıyor ({ses_modu} → {_ses_modu_sesi(ses_modu)}); tekrar TTS üretilmiyor.')
    else:
        log_ekle('❌ Güvenli TTS üretilemedi.')
    log_ekle('🏁 Metin üretimi tamamlandı; video render atlandı.')
    return _payload(reels_state, caption_state, threads_state, ses_basarili, ses_dosyasi,
                   legacy_voice, model_reels, kullanilan_ses_modeli, model_threads, ses_modu,
                   qa_rounds, '', '', fact_state, editorial_state, duo_plan, duo_script,
                   qa_state, qa_pass, state, mode='text')
