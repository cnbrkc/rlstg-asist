"""
pipeline.py — Ultimate Content Engine orkestratörü.

Streamlit bağımlılığı yoktur; Telegram/GitHub Actions tarafından doğrudan çağrılabilir.
"""
import json
import os, re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from core.config import KELIME_HIZI_ORANI, SES_HIZ_CARPANI, PIPELINE_ADIMLARI
from core.schemas import VIDEO_ANALYSIS_SCHEMA, FACT_LOCK_SCHEMA, EDITORIAL_SCHEMA, REELS_CREATIVE_SCHEMA, CAPTION_SCHEMA, THREADS_SCHEMA, QA_SCHEMA, DUO_SCRIPT_SCHEMA
from core.prompts import (forensic_analiz_promptunu_olustur, research_promptunu_olustur, editorial_promptunu_olustur,
                     reels_creative_promptunu_olustur, caption_promptunu_olustur, threads_promptunu_olustur,
                     qa_promptunu_olustur, durumu_metne_donustur, girdi_birlestir,
                     _reels_kelime_ayarlarini_hazirla)
from core.media import (
    gecici_ses_yolu,
    gecici_dosya_yolu,
    temp_dosya_temizle,
    video_ve_sesi_birlestir,
    _ses_suresini_al,
    medya_raporu,
    video_suresini_al,
)
from duo.duo_strategy import normalize_duo_strategy
from duo.duo_script_engine import (
    build_duo_generation_contract,
    build_generation_prompt,
    duo_conversation_quality_issues,
    validate_generated_duo,
)
from duo.duo_audio import duo_ses_uret

TOPLAM_ADIM = len(PIPELINE_ADIMLARI)
VOICE_REGEN_MAX = 2
VOICE_DURATION_MIN_RATIO = 0.85
VOICE_DURATION_MAX_RATIO = 1.15
MAX_QA_REGEN = 1

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
        return {}
    idx = reels_state.get('secilen_aile_index', 0)
    return families[idx] if isinstance(idx, int) and 0 <= idx < len(families) else families[0]


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
    content = girdi_birlestir(
        durumu_metne_donustur('VIDEO IDENTITY',video_state.get('video_identity',{})),
        durumu_metne_donustur('OBSERVED FACTS',video_state.get('observed_facts',[])),
        durumu_metne_donustur('UNKNOWNS',video_state.get('unknowns',[])),
        durumu_metne_donustur('POSSIBLE INFERENCE',video_state.get('possible_inference',[])),
        durumu_metne_donustur('ARAŞTIRMA İHTİYAÇLARI',video_state.get('viral_arastirma_ihtiyaclari',[]))
    )
    return _run_timed(
        log, "Research / Fact Lock (Gemini + Search/fallback)",
        lambda: router.metin_uret(content,research_promptunu_olustur(),FACT_LOCK_SCHEMA,log,arama_kullan=True),
    )


def _editorial_oncelik_denetimi(editorial_state, log):
    """Modelin puanladığı adaylarla seçimi karşılaştırıp sonraki katmanları uyarır.

    Yaratıcı kararı yerelde körlemesine değiştirmez; olası öncelik sapmasını
    görünür ve Reels/QA tarafından okunabilir hale getirir.
    """
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


def _reels_creative_calistir(router, editorial_state, fact_state, video_state, notes, sure_saniye, ton, log, kelime_hizi_orani=None, ek_talimat=""):
    content = girdi_birlestir(durumu_metne_donustur('VIDEO STATE',video_state),durumu_metne_donustur('FACT LOCK',fact_state),durumu_metne_donustur('EDITORIAL',editorial_state),notes or '')
    prompt = reels_creative_promptunu_olustur(sure_saniye,ton,kelime_hizi_orani,ek_talimat=ek_talimat)
    result, model = _run_timed(
        log, "Reels Creative (Gemini)",
        lambda: router.metin_uret(content,prompt,REELS_CREATIVE_SCHEMA,log,arama_kullan=False),
    )
    result = _object_state_or_empty(result)
    return result, model


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


def _sosyal_ciktilari_paralel_uret(router, reels_state, fact_state, editorial_state, video_state, log, ton=None):
    """Birbirine bağımlı olmayan Caption ve Threads isteklerini eşzamanlı üretir.

    İki çıktı da aynı kilitli Fact Lock/Editorial girdisini kullanır; birbirinin
    sonucunu tüketmediği için seri beklemek kalite sağlamıyor, yalnızca wall time
    ekliyordu. Her kol kendi mevcut guard/fallback davranışını korur.
    """
    started = time.perf_counter()
    log("⚡ Caption ve Threads bağımsız kolları paralel başlatılıyor.")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="social-output") as executor:
        caption_future = executor.submit(
            _caption_calistir, router, reels_state, fact_state, editorial_state, video_state, log, ton
        )
        threads_future = executor.submit(
            _threads_calistir, router, video_state, fact_state, editorial_state, log, ton
        )
        try:
            caption_state, model_caption = caption_future.result()
        except Exception as exc:
            log(f"⚠️ Caption paralel kolu hata verdi: {str(exc)[:160]}")
            caption_state, model_caption = {"reels_aciklamasi":"", "reels_hashtagleri":[]}, "hata"
        try:
            threads_state, model_threads = threads_future.result()
        except Exception as exc:
            log(f"⚠️ Threads paralel kolu hata verdi: {str(exc)[:160]}")
            threads_state, model_threads = {"threads_aciklamasi":""}, "hata"
    elapsed = time.perf_counter() - started
    log(f"⚡ Caption + Threads paralel duvar süresi: {elapsed:.2f}s")
    return (
        _caption_state_normalize(caption_state), model_caption,
        _threads_state_normalize(threads_state), model_threads,
    )


def _kelime_sayisi(metin):
    return len(re.findall(r"\b[\wÇĞİÖŞÜçğıöşüÀ-ÿ]+(?:[-'][\wÇĞİÖŞÜçğıöşüÀ-ÿ]+)*\b", str(metin or ""), re.UNICODE))


def _reels_kelime_kontrolu(reels_state, sure_saniye, kelime_hizi_orani=None):
    reels_state = _object_state_or_empty(reels_state)
    hedef, minimum, maksimum, _, _ = _reels_kelime_ayarlarini_hazirla(sure_saniye, kelime_hizi_orani or KELIME_HIZI_ORANI)
    adet = _kelime_sayisi(reels_state.get('seslendirme_metni',''))
    return adet, hedef, minimum, maksimum


def _duo_kelime_sayisi(duo_script):
    if not duo_script or not isinstance(duo_script,dict) or not duo_script.get('segments'):
        return 0
    return sum(_kelime_sayisi(seg.get('text','')) for seg in duo_script.get('segments',[]) if isinstance(seg,dict))


def _explicit_voice_mode_from_notes(notes):
    """Kullanıcı notundaki açık ses modu talebini dar ve güvenli biçimde çöz."""
    text = str(notes or "").casefold().replace("_", " ")
    # Kullanıcı kararı özellikle AI'ya bırakıyorsa mod kelimelerini override
    # sayma (örn. "solo mu duo mu videoya göre sen seç").
    if re.search(r"\b(sen\s+seç|ai.{0,20}karar\s+ver|içeriğe\s+göre\s+seç|videoya\s+göre\s+seç)\b", text):
        return ""

    duo_pattern = r"\b(duo|dual|iki\s+ses(?:li)?|çift\s+ses(?:li)?)\b"
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


def _duo_plan_hazirla(reels_state, sure_saniye, ton, notes=""):
    reels_state = _object_state_or_empty(reels_state)
    mode_request = _explicit_voice_mode_from_notes(notes)
    if mode_request:
        reels_state = dict(reels_state)
        if mode_request == "SOLO":
            raw_strategy = reels_state.get("duo_stratejisi") or {}
            ai_mode = str(reels_state.get("anlatim_modu") or raw_strategy.get("uygunluk") or "").upper()
            if ai_mode in {"SOLO_FEMALE", "SOLO_MALE"}:
                mode_request = ai_mode
            else:
                try:
                    female_weight = float(raw_strategy.get("female_agirligi") or 0)
                    male_weight = float(raw_strategy.get("male_agirligi") or 0)
                except (TypeError, ValueError):
                    female_weight = male_weight = 0
                hook = str(raw_strategy.get("hook_speaker") or "").lower()
                mode_request = "SOLO_MALE" if male_weight > female_weight or (male_weight == female_weight and hook == "male") else "SOLO_FEMALE"
        reels_state["_explicit_voice_mode"] = mode_request
    strategy = normalize_duo_strategy(reels_state)
    hedef, minimum, maksimum, _, _ = _reels_kelime_ayarlarini_hazirla(sure_saniye, KELIME_HIZI_ORANI)
    strategy["target_words"] = hedef
    strategy["min_words"] = minimum
    strategy["max_words"] = maksimum
    strategy["content_tone"] = str(ton or "dengeli").strip().lower() or "dengeli"
    return strategy


def _duo_script_calistir(router, duo_plan, editorial_state, fact_state, video_state, log, regeneration_instruction=""):
    contract = build_duo_generation_contract(duo_plan)
    editorial = durumu_metne_donustur('EDITORIAL', editorial_state)
    facts = durumu_metne_donustur('FACT LOCK', fact_state)
    video = durumu_metne_donustur('VIDEO', video_state)
    context = girdi_birlestir(editorial, video, facts)
    instruction = regeneration_instruction or ""
    max_attempts = 2 if contract.get("mode") == "DUO" else 1
    valid_candidates = []
    last_error = ""

    for attempt in range(max_attempts):
        prompt = build_generation_prompt(
            contract,
            editorial_context=girdi_birlestir(editorial, video),
            fact_lock=facts,
            regeneration_instruction=instruction,
        )
        label = "DUO diyalog senaryosu (Gemini)" if attempt == 0 else "DUO doğal muhabbet kalite yenilemesi (Gemini)"
        try:
            generated, model = _run_timed(
                log, label,
                lambda: router.metin_uret(context, prompt, DUO_SCRIPT_SCHEMA, log, arama_kullan=False),
            )
            segments = validate_generated_duo(contract, generated)
            if not segments:
                raise ValueError('Duo script doğrulama sonrası boş kaldı.')
            issues = duo_conversation_quality_issues(contract, generated)
            design = generated.get("conversation_design", {}) if isinstance(generated, dict) else {}
            candidate = {
                "contract": contract,
                "conversation_design": design,
                "segments": segments,
                "model": model,
                "status": "ready",
                "conversation_quality_issues": issues,
            }
            valid_candidates.append(candidate)
            if not issues:
                log("✅ DUO doğal muhabbet denetimi geçti: karşılık, ritim, dönüş ve payoff hazır.")
                return candidate
            if attempt + 1 < max_attempts:
                log(f"⚠️ DUO muhabbet yapısı zayıf ({', '.join(issues)}); yalnız script için tek kalite yenilemesi başlatılıyor.")
                instruction = (
                    "Önceki script yapısal olarak geçerliydi fakat doğal kısa-video sohbeti denetiminde şu sorunları verdi: "
                    + ", ".join(issues)
                    + ". Gerçek lexical uptake, asimetrik replik uzunluğu, ilk iki turda doğrudan karşılık, "
                      "anlamlı fikir dönüşü ve hook'a payoff üret; olguları değiştirme."
                )
        except Exception as exc:
            last_error = str(exc)[:180]
            if attempt + 1 < max_attempts:
                log(f"⚠️ DUO script denemesi doğrulanamadı; tek kontrollü yenileme yapılacak: {last_error}")
                instruction = "Önceki çıktı doğrulanamadı. Şemaya tam uy, iki speakerı da gerçek karşılıklı muhabbet içinde kullan."
                continue

    if valid_candidates:
        best = min(valid_candidates, key=lambda item: len(item.get("conversation_quality_issues") or []))
        log(
            "⚠️ DUO kalite yenilemesi tüm işaretleri temizleyemedi; mekanik split fallback yerine en iyi geçerli doğal script korunuyor: "
            + ", ".join(best.get("conversation_quality_issues") or [])
        )
        return best

    log(f'⚠️ Konuşma scripti üretimi başarısız; mod sözleşmesine uygun yeniden üretim gerekecek: {last_error or "bilinmeyen hata"}')
    return {"contract": contract, "segments": [], "model": "hata", "status": "fallback", "error": last_error}


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


def _reels_ve_ses_uyumlu_uret(router, editorial_state, fact_state, video_state, notes, sure_saniye, ton, legacy_voice, log, baslangic_talimati=""):
    ek_talimat = baslangic_talimati or ""
    son_reels={}; son_model='hata'; son_duo_plan={}; son_duo_script={}; son_ses=''; son_info=None; son_mod='LEGACY'

    for deneme in range(VOICE_REGEN_MAX+1):
        reels_state,model_reels=_reels_creative_calistir(router,editorial_state,fact_state,video_state,notes,sure_saniye,ton,log,KELIME_HIZI_ORANI,ek_talimat=ek_talimat)
        son_reels,son_model=reels_state,model_reels
        adet,hedef,minimum,maksimum=_reels_kelime_kontrolu(reels_state,sure_saniye,KELIME_HIZI_ORANI)
        log(f'📝 Seslendirme uzunluk kontrolü: {adet} kelime | hedef {hedef} | izin verilen {minimum}-{maksimum}')

        duo_plan=_duo_plan_hazirla(reels_state,sure_saniye,ton,notes=notes)
        log('🗣️ Konuşma metni hazırlanıyor...' if deneme==0 else f'🗣️ Konuşma metni yenileniyor ({deneme}/{VOICE_REGEN_MAX})...')
        duo_script=_duo_script_calistir(router,duo_plan,editorial_state,fact_state,video_state,log)
        son_duo_plan,son_duo_script=duo_plan,duo_script
        duo_adet=_duo_kelime_sayisi(duo_script)

        kelime_sorunu = adet < minimum or adet > maksimum
        if duo_script.get('status')=='ready' and duo_adet:
            kelime_sorunu = kelime_sorunu or duo_adet < minimum or duo_adet > maksimum
            if duo_adet != adet:
                log(f'🗣️ Nihai TTS scripti: {duo_adet} kelime | Reels metni: {adet} kelime')

        if kelime_sorunu and deneme < VOICE_REGEN_MAX:
            ek_talimat=(f'Önceki üretim {adet} kelime, nihai konuşma scripti {duo_adet} kelimeydi; hedef {hedef}, izin verilen {minimum}-{maksimum}. '
                        'Bu kez SESLENDİRME ve nihai konuşma metnini mutlaka bu aralıkta tut.')
            log(f'⚠️ Nihai seslendirme kelime aralığı dışında; yeniden üretim başlatılıyor ({deneme+1}/{VOICE_REGEN_MAX}).')
            continue

        ses_dosyasi=gecici_ses_yolu()
        ok,info,mod=_duo_ses_veya_legacy_uret(router,duo_script,reels_state.get('seslendirme_metni',''),legacy_voice,log,ses_dosyasi)
        if not ok:
            temp_dosya_temizle(ses_dosyasi)
            if deneme < VOICE_REGEN_MAX:
                ek_talimat='Seslendirme doğal okunabilirlikte ve hedef kelime aralığında olsun.'
                continue
            return reels_state,model_reels,duo_plan,duo_script,False,None,'LEGACY',''

        uyumlu,ses_suresi,oran=_ses_sure_uyumlu_mu(ses_dosyasi,sure_saniye)
        log(f'🎚️ TTS gerçek süre kontrolü: video {sure_saniye:.2f}s → ses {ses_suresi:.2f}s | oran {oran:.2f}x')
        if os.path.exists(ses_dosyasi) and ses_suresi > 0:
            if not uyumlu:
                log(f'🎚️ TTS/video oranı {oran:.2f}x; yeniden TTS üretmek yerine video senkron katmanına bırakılıyor.')
            return reels_state,model_reels,duo_plan,duo_script,True,info,mod,ses_dosyasi

        if mod in {'SOLO_FEMALE','SOLO_MALE'}:
            legacy_path=gecici_ses_yolu()
            legacy_voice_for_mode=_mod_icin_legacy_ses(mod,legacy_voice)
            legacy_ok,legacy_info=router.ses_uret(reels_state.get('seslendirme_metni',''),legacy_voice_for_mode,legacy_path,log,hiz_carpani=SES_HIZ_CARPANI)
            legacy_uyum,legacy_sure,legacy_oran=_ses_sure_uyumlu_mu(legacy_path,sure_saniye)
            if legacy_ok and legacy_uyum:
                temp_dosya_temizle(ses_dosyasi)
                log(f'↩️ {mod} TTS süreye sığmadı; {legacy_voice_for_mode} legacy geri dönüşü kullanıldı ({legacy_oran:.2f}x).')
                return reels_state,model_reels,duo_plan,duo_script,True,legacy_info,'LEGACY_'+mod,legacy_path
            temp_dosya_temizle(legacy_path)

        if deneme < VOICE_REGEN_MAX:
            temp_dosya_temizle(ses_dosyasi)
            ek_talimat=(f'TTS önceki metni {ses_suresi:.2f} saniye üretti; hedef video {sure_saniye:.2f} saniye. '
                        f'Hedef kelime sayısı yaklaşık {hedef}, kesin aralık {minimum}-{maksimum}.')
            continue

        return reels_state,model_reels,duo_plan,duo_script,True,info,mod,ses_dosyasi

    return son_reels,son_model,son_duo_plan,son_duo_script,False,son_info,son_mod,son_ses


def _duo_ve_ses_yenile(router,reels_state,duo_plan,editorial_state,fact_state,video_state,sure_saniye,legacy_voice,log,regen_instruction):
    instruction=regen_instruction or 'Duo script QA tarafından başarısız bulundu.'
    mod = _beklenen_gercek_mod(duo_plan, None)
    duo_script = {"status":"fallback","segments":[],"contract":duo_plan or {}}
    for deneme in range(VOICE_REGEN_MAX+1):
        duo_script=_duo_script_calistir(router,duo_plan,editorial_state,fact_state,video_state,log,regeneration_instruction=instruction)
        if duo_script.get('status') != 'ready':
            if deneme < VOICE_REGEN_MAX:
                instruction += ' Önceki Duo üretimi doğrulanamadı.'
                continue
            return duo_script,False,None,mod
        ses_dosyasi=gecici_ses_yolu()
        ok,info,mod=_duo_ses_veya_legacy_uret(router,duo_script,reels_state.get('seslendirme_metni',''),legacy_voice,log,ses_dosyasi)
        if not ok:
            temp_dosya_temizle(ses_dosyasi)
            if deneme < VOICE_REGEN_MAX:
                instruction += ' TTS üretimi başarısız oldu.'
                continue
            return duo_script,False,None,mod
        uyumlu,ses_suresi,oran=_ses_sure_uyumlu_mu(ses_dosyasi,sure_saniye)
        log(f'🎚️ QA sonrası TTS süre kontrolü: video {sure_saniye:.2f}s → ses {ses_suresi:.2f}s | oran {oran:.2f}x')
        if os.path.exists(ses_dosyasi) and ses_suresi > 0:
            if not uyumlu:
                log(f'🎚️ QA TTS/video oranı {oran:.2f}x; video senkron katmanına bırakılıyor.')
            return duo_script,True,(info,ses_dosyasi),mod
        temp_dosya_temizle(ses_dosyasi)
    return duo_script,False,None,mod


def _ses_modu_sesi(mode):
    return {'SOLO_FEMALE':'Autonoe','SOLO_MALE':'Charon','DUO':'Autonoe + Charon'}.get(mode,mode or 'Bilinmiyor')


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


def _qa_regeneration_loop(router,video_state,fact_state,editorial_state,reels_state,caption_state,threads_state,duo_plan,duo_script,sure_saniye,ton,legacy_voice,log,voice_initial_instruction='',production_notes=''):
    qa_state={}; qa_rounds=0; ses_basarili=False; kullanilan_ses_modeli=None; ses_modu='LEGACY'; ses_dosyasi=''
    reels_state,model_reels,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi=_reels_ve_ses_uyumlu_uret(
        router,editorial_state,fact_state,video_state,production_notes,sure_saniye,ton,legacy_voice,log,baslangic_talimati=voice_initial_instruction
    )
    caption_state,model_caption,threads_state,model_threads=_sosyal_ciktilari_paralel_uret(
        router,reels_state,fact_state,editorial_state,video_state,log,ton
    )

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
                return reels_state,caption_state,threads_state,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi,qa_state,qa_rounds,model_reels,model_caption,model_threads,True

        if not supported_targets:
            break
        if qa_round >= MAX_QA_REGEN:
            break

        target_set=set(supported_targets)
        creative_needed=bool(target_set & {'VOICEOVER_FAIL','COVER_FAIL'})
        duo_needed='DUO_SCRIPT_FAIL' in target_set and not creative_needed
        downstream_caption=bool(target_set & {'VOICEOVER_FAIL','COVER_FAIL','CAPTION_FAIL'})
        downstream_threads='THREADS_FAIL' in target_set
        instruction='QA regeneration: ' + ', '.join(supported_targets) + '.'

        if creative_needed:
            reels_state,model_reels,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi=_reels_ve_ses_uyumlu_uret(
                router,editorial_state,fact_state,video_state,production_notes,sure_saniye,ton,legacy_voice,log,baslangic_talimati=instruction
            )
        elif duo_needed:
            duo_script,ses_basarili,duo_info,ses_modu=_duo_ve_ses_yenile(router,reels_state,duo_plan,editorial_state,fact_state,video_state,sure_saniye,legacy_voice,log,instruction)
            if ses_basarili and duo_info:
                kullanilan_ses_modeli,ses_dosyasi=duo_info
            else:
                ses_dosyasi=''

        if downstream_caption:
            try:
                caption_state,model_caption=_caption_calistir(router,reels_state,fact_state,editorial_state,video_state,log,ton)
            except Exception:
                caption_state={"reels_aciklamasi":"","reels_hashtagleri":[]}
            caption_state = _caption_state_normalize(caption_state)
        if downstream_threads:
            try:
                threads_state,model_threads=_threads_calistir(router,video_state,fact_state,editorial_state,log,ton)
            except Exception:
                threads_state={"threads_aciklamasi":""}
            threads_state = _threads_state_normalize(threads_state)

    return reels_state,caption_state,threads_state,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi,qa_state,qa_rounds,model_reels,model_caption,model_threads,False


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
    editorial_state,_=_editorial_calistir(router,video_state,fact_state,metin_uretim_notlari,log_ekle,icerik_tonu); state['editorial_state']=editorial_state
    _ilerleme(ilerlemeyi_guncelle,4); log_ekle('🎙️ Reels hazırlanıyor (Cover + Hook + Voiceover + Duo)...')
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
        log_ekle('⚠️ Render öncesi hazır TTS dosyası bulunamadı; recovery başlatılıyor (1/1).')
        recovery_path=gecici_ses_yolu()
        try:
            recovery_ok,recovery_info,recovery_mode=_duo_ses_veya_legacy_uret(
                router,duo_script,_object_state_or_empty(reels_state).get('seslendirme_metni',''),legacy_voice,log_ekle,recovery_path
            )
        except Exception as exc:
            recovery_ok,recovery_info,recovery_mode=False,None,ses_modu
            log_ekle(f'❌ TTS recovery başarısız: {str(exc)[:180]}')
        if recovery_ok and os.path.exists(recovery_path):
            recovery_uyumlu,recovery_sure,recovery_oran=_ses_sure_uyumlu_mu(recovery_path,sure_saniye)
            log_ekle(f'🎚️ Recovery TTS süre kontrolü: video {sure_saniye:.2f}s → ses {recovery_sure:.2f}s | oran {recovery_oran:.2f}x')
            if recovery_sure > 0:
                ses_dosyasi=recovery_path
                kullanilan_ses_modeli=recovery_info
                ses_modu=recovery_mode
                state['ses_modu']=ses_modu
                if recovery_uyumlu:
                    log_ekle(f'✅ TTS recovery başarılı: {ses_modu} → {_ses_modu_sesi(ses_modu)}')
        else:
            temp_dosya_temizle(recovery_path)
            ses_basarili=False
            ses_dosyasi=''

    if not ses_basarili or not ses_dosyasi or not os.path.exists(ses_dosyasi):
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
    _ilerleme(ilerlemeyi_guncelle,3,'🧠 Editorial Brain'); editorial_state,_=_editorial_calistir(router,video_state,fact_state,metin,log_ekle,icerik_tonu); state['editorial_state']=editorial_state
    _ilerleme(ilerlemeyi_guncelle,4,'🎙️ Reels Creative'); legacy_voice = secilen_ses_ingilizce if isinstance(secilen_ses_ingilizce,str) and secilen_ses_ingilizce.strip() else 'Autonoe'
    reels_state,model_reels,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi,caption_state,threads_state,qa_state,qa_rounds,model_caption,model_threads,qa_pass=_qa_regeneration_loop(
        router,video_state,fact_state,editorial_state,{}, {},{}, {},{},sure_saniye,icerik_tonu,legacy_voice,log_ekle, production_notes=metin
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
