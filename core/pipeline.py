"""
pipeline.py — Ultimate Content Engine orkestratörü.
Agentic (4 Ajanlı) Viral İçerik Üretim Mimarisi.
"""
import json
import os, re
import shutil
import time
from contextlib import contextmanager
from core.web_search import web_arastirma_yap
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
from core.narration_mode import anlatim_modu_karar_ver
from core.cover_titles import (
    ALTERNATIF_SAYISI as KAPAK_ALTERNATIF_SAYISI,
    kapak_basliklarini_normalize_et,
    kapak_basliklarini_metne_dok,
)
from duo.duo_audio import duo_ses_uret

TOPLAM_ADIM = len(PIPELINE_ADIMLARI)
MAX_QA_REGEN = 1
VOICE_REGEN_MAX = 2
VOICE_DURATION_MIN_RATIO = 0.85
VOICE_DURATION_MAX_RATIO = 1.15
# Kelime aralığı dışındaki senaryo yalnızca sapma bu oranın ÜZERİNDEYSE yeniden
# yazdırılır (hedefe göre). Eylül 2026 logunda 137 kelime (hedef 140-170) için
# bile tam Script+Critic turu tekrarlanıyordu; küçük sapmayı FFmpeg senkron
# katmanı (0.5x-1.5x video hızı) zaten kapatıyor.
VOICE_WORD_TOLERANCE_RATIO = 0.20

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
        # Metadata Generator ajanı METADATA_GEN_SCHEMA ile `reels_aciklama` /
        # `reels_hashtag` döndürür; eski Caption ajanı ise `reels_aciklamasi` /
        # `reels_hashtagleri`. İkisi de aynı sosyal çıktıya eşlenir.
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
        basliklar = reels_state.get("kapak_basliklari") or [{}]
        ilk = basliklar[0] if isinstance(basliklar[0], dict) else {}
        return {"kapak_metni": ilk.get("ana") or ilk.get("ust") or "", "kapak_alt": ilk.get("alt", "")}
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
    with _istek_profili(router, "uzun_metin"):
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
    with _istek_profili(router, "uzun_metin"):
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
    if _yakin_zamanda_asiri_yuk(router):
        # Karar verilemezse pipeline zaten varsayılan DUO ile ilerliyor; yoğunlukta
        # bu isteğe bağlı çağrı (logda 67 sn boşa gitti) hiç yapılmaz.
        log('⏭️ Anlatım modu kararı atlandı: API yakın zamanda tam tur aşırı yük verdi; varsayılan mod kullanılacak.')
        return editorial
    with _hizli_basarisizlik(router), _istek_profili(router, "istege_bagli"):
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

def _ses_modu_sesi(mode):
    return {'SOLO_FEMALE':'Autonoe','SOLO_MALE':'Charon','DUO':'Autonoe + Charon'}.get(mode, mode or 'Bilinmiyor')


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

KAPAK_FORMAT_KURALI = (
    "🚨 [Kural: Reels Kapak Yazısı Formatı] — İSTİSNASIZ:\n"
    f"kapak_basliklari alanına TAM {KAPAK_ALTERNATIF_SAYISI} FARKLI kapak alternatifi yaz; tek başlık ASLA kabul edilmez.\n"
    "Her alternatif iki katmandan oluşur:\n"
    "  • ust (ÜST BAŞLIK): Dikkat çekici kanca. TAMAMI BÜYÜK HARF. 2-4 kelime. Amaç: kaydırmayı durdurmak, merak yaratmak.\n"
    "  • alt (Alt başlık): Tamamlayıcı detay. Cümle düzeni (yalnızca ilk harf büyük, gerisi küçük). 4-7 kelime. "
    "Amaç: merakı açıklamak, izlemek için sebep vermek; cevabı vermez.\n"
    "Her alternatif farklı açı/duygu/formül kullansın (şok rakam, efsane çürütme, negatif uyarı, Türkiye mağduriyeti, ters köşe). "
    "Üst ve alt aynı cümleyi tekrar etmesin; marka adını tek başına başlık yapma; 'YENİ VİDEO' gibi boş kalıplar yasak.\n"
    "kapak_metni alanına en güçlü alternatifin ust değerini aynen yaz.\n"
    "Örnek: ust='BU SUV NORMAL DEĞİL' / alt='Aile arabası gibi duruyor ama değil'."
)


def _hook_gen_calistir(router, detective_state, fact_state, editorial_state, log):
    prompt = (
        "Sen otoXtra'nın Kışkırtıcı Kanca Üreticisisin. Dedektifin bulduğu malzemeyi kullanarak "
        "ilk 3 saniyede izleyiciyi şok edecek bir kanca üreteceksin.\n"
        "Şablonlardan birini seç: Efsane_Curutme, Ters_Kose, Negatif_Uyari.\n"
        "Kapak metni ve ilk 3 saniye kancası ultra viral ve iddialı olmalı; ilk 3 saniye kancası kapak başlığını birebir tekrar etmemeli.\n\n"
        + KAPAK_FORMAT_KURALI
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


def _critic_skoru(critic_state):
    try:
        return float(critic_state.get("score", 10))
    except (TypeError, ValueError):
        return 10.0


def _critic_onayladi_mi(critic_state):
    approved = critic_state.get("approved", False)
    if isinstance(approved, str):
        approved = approved.strip().lower() in {"true", "evet", "yes", "1"}
    return bool(approved) or _critic_skoru(critic_state) >= 7


def _hook_fallback(editorial_state):
    """Hook ajanı geçici olarak kullanılamazsa Editorial brief'ten güvenli kanca türetir."""
    editorial = _object_state_or_empty(editorial_state)
    territories = editorial.get("potential_hook_territories")
    ilk = ""
    if isinstance(territories, list):
        ilk = next((str(x).strip() for x in territories if str(x).strip()), "")
    elif isinstance(territories, str):
        ilk = territories.strip()
    core_story = str(editorial.get("core_story") or "").strip()
    basliklar = kapak_basliklarini_normalize_et(None, editorial, {}, {"ilk_3_saniye_kanca": ilk or core_story})
    return {
        "secilen_sablon": "Ters_Kose",
        "kapak_basliklari": basliklar,
        "kapak_metni": basliklar[0]["ust"] if basliklar else " ".join((ilk or core_story).split()[:4]),
        "ilk_3_saniye_kanca": ilk or core_story,
    }


def _kapak_basliklarini_hazirla(hook_state, editorial_state, detective_state, log):
    """Hook ajanının kapak setini kurala göre doğrular; eksikse 5'e tamamlar ve
    hook_state'i güncellenmiş kapak seti ile döndürür (ana == ust)."""
    hook_state = dict(_object_state_or_empty(hook_state))
    ham = hook_state.get("kapak_basliklari") or hook_state.get("kapak_metni") or ""
    basliklar = kapak_basliklarini_normalize_et(ham, editorial_state, detective_state, hook_state, log)
    hook_state["kapak_basliklari"] = basliklar
    hook_state["kapak_metni"] = basliklar[0]["ust"] if basliklar else str(hook_state.get("kapak_metni") or "")
    log("🎯 Kapak başlıkları (Üst/Alt) hazır:\n" + kapak_basliklarini_metne_dok(basliklar))
    return hook_state, basliklar


@contextmanager
def _hizli_basarisizlik(router):
    """Güvenli varsayılanı olan adımlarda router'ın aşırı-yük beklemesini kapatır;
    bekleme bütçesi zorunlu adımlara (Script Writer, TTS, Forensic...) kalır."""
    ctx = getattr(router, "hizli_basarisizlik", None)
    if callable(ctx):
        with ctx():
            yield
    else:
        yield


@contextmanager
def _istek_profili(router, profil):
    """Router destekliyorsa bloktaki isteklere zaman aşımı/bütçe profili uygular."""
    ctx = getattr(router, "istek_profili", None)
    if callable(ctx):
        with ctx(profil):
            yield
    else:
        yield


def _yakin_zamanda_asiri_yuk(router):
    fn = getattr(router, "yakin_zamanda_asiri_yuk_var_mi", None)
    try:
        return bool(fn()) if callable(fn) else False
    except Exception:
        return False


def _istege_bagli_ajan(log, etiket, callback, varsayilan, router=None, atlanabilir=False):
    """Zenginleştirici ajan (Detective/Hook/Critic/Metadata) API'si geçici olarak
    tamamen düşerse tüm pipeline'ı öldürmek yerine güvenli varsayılanla devam eder.

    atlanabilir=True: Router yakın zamanda TAM TUR aşırı yük gördüyse ajan hiç
    denenmez (Eylül 2026 logunda Detective 77 sn, Critic 236 sn boşa harcadı);
    güvenli varsayılanla anında devam edilir."""
    if atlanabilir and _yakin_zamanda_asiri_yuk(router):
        log(f"⏭️ {etiket} atlandı: API yakın zamanda tam tur aşırı yük verdi; güvenli varsayılanla devam ediliyor.")
        return varsayilan, "atlandi"
    try:
        with _hizli_basarisizlik(router), _istek_profili(router, "istege_bagli"):
            state, model = callback()
    except Exception as exc:
        log(f"⚠️ {etiket} kullanılamadı; güvenli varsayılanla devam ediliyor: {type(exc).__name__}: {str(exc)[:160]}")
        return varsayilan, "hata"
    return _object_state_or_empty(state), model


def agentic_icerik_uretimi(router, video_state, fact_state, editorial_state, sure_saniye, ton, legacy_voice, log, mod_karari=None):
    """4 Ajanlı Viral Üretim Döngüsü (Kelime ve TTS Süre Güvenlik Duvarlı)"""
    
    mod = str((mod_karari or {}).get("mode") or "DUO").strip().upper()
    if mod not in {"DUO", "SOLO_FEMALE", "SOLO_MALE"}:
        mod = "DUO"
    hedef, minimum, maksimum, _, _ = _reels_kelime_ayarlarini_hazirla(sure_saniye, KELIME_HIZI_ORANI)
    hedef_kelime_bilgisi = f"Hedef {hedef} kelime. Kesin aralık {minimum}-{maksimum} kelime."
    
    # 1. Detective (Sadece ilk denemede çalışır, veri değişmez; API yoğunsa atlanır)
    detective_state, _ = _istege_bagli_ajan(
        log, "🕵️ Detective Ajan",
        lambda: _detective_calistir(router, video_state, fact_state, editorial_state, log),
        {},
        router=router,
        atlanabilir=True,
    )
    
    # 2. Hook Gen (Sadece ilk denemede çalışır) — kapak seti burada üretilir.
    hook_state, _ = _istege_bagli_ajan(
        log, "🪝 Hook Generator Ajan",
        lambda: _hook_gen_calistir(router, detective_state, fact_state, editorial_state, log),
        _hook_fallback(editorial_state),
        router=router,
    )
    if not str(hook_state.get("kapak_metni") or "").strip() and not hook_state.get("kapak_basliklari"):
        hook_state = {**_hook_fallback(editorial_state), **{k: v for k, v in hook_state.items() if v}}
    # [Kural: Reels Kapak Yazısı Formatı] — her zaman 5 Üst/Alt alternatifi.
    hook_state, kapak_basliklari = _kapak_basliklarini_hazirla(hook_state, editorial_state, detective_state, log)
    
    son_reels = {}
    son_duo_plan = {}
    son_duo_script = {}
    son_model = 'hata'
    
    # TTS ve Kelime Güvenlik Döngüsü
    for deneme in range(VOICE_REGEN_MAX + 1):
        
        # 3. Script Writer (zorunlu ajan: router'ın aşırı-yük tekrarlarına rağmen
        # düşerse hata yukarı taşınır)
        script_state, model_script = _script_writer_calistir(
            router, hook_state, detective_state, fact_state, editorial_state, 
            video_state, sure_saniye, log, hedef_kelime_bilgisi=hedef_kelime_bilgisi, mod=mod
        )
        script_state = _object_state_or_empty(script_state)
        son_model = model_script
        
        # 4. Critic (isteğe bağlı: düşerse/atlanırsa senaryo onaylı sayılır)
        critic_state, _ = _istege_bagli_ajan(
            log, "🔥 Critic Ajan",
            lambda: _critic_calistir(router, script_state, hook_state, log),
            {"score": 10, "approved": True, "feedback": ""},
            router=router,
            atlanabilir=True,
        )
        
        # Critic Döngüsü (MAX 1 Revize)
        if not _critic_onayladi_mi(critic_state):
            feedback = critic_state.get("feedback") or "Daha doğal ve kışkırtıcı yap."
            log(f"⚠️ Critic onaylamadı (Score: {critic_state.get('score')}). Revize başlatılıyor...")
            try:
                revize_state, revize_model = _script_writer_calistir(
                    router, hook_state, detective_state, fact_state, editorial_state, 
                    video_state, sure_saniye, log, feedback=feedback, hedef_kelime_bilgisi=hedef_kelime_bilgisi, mod=mod
                )
                revize_state = _object_state_or_empty(revize_state)
                if revize_state.get("segments"):
                    script_state, son_model = revize_state, revize_model
                else:
                    log("⚠️ Revize senaryo boş döndü; ilk senaryo korunuyor.")
            except Exception as exc:
                log(f"⚠️ Critic revizesi üretilemedi; ilk senaryo korunuyor: {type(exc).__name__}: {str(exc)[:160]}")
            
        # Reels State Emülasyonu
        segments = [seg for seg in (script_state.get("segments") or []) if isinstance(seg, dict)]
        soru = str(script_state.get("yorum_tetikleyici_soru") or "").strip()
        full_text = " ".join(str(seg.get("text", "") or "").strip() for seg in segments)
        full_text = f"{full_text} {soru}".strip()
        secili_kapak = kapak_basliklari[0] if kapak_basliklari else {"ust": hook_state.get("kapak_metni", ""), "alt": ""}
        reels_state = {
            "seslendirme_metni": full_text,
            # [Kural: Reels Kapak Yazısı Formatı] 5 alternatif; her biri ust (2-4 kelime, BÜYÜK)
            # + alt (4-7 kelime, cümle düzeni). `ana` == ust (geriye dönük uyumluluk).
            "kapak_basliklari": [dict(x) for x in kapak_basliklari],
            "kapak_format": "ust_alt_5",
            "hook_families": [{
                "kapak_ana": secili_kapak.get("ust", ""),
                "kapak_alt": secili_kapak.get("alt", ""),
                "ilk_uc_saniye": hook_state.get("ilk_3_saniye_kanca", ""),
            }],
            "metadata": {}
        }
        
        # TTS Segmentlerini Hazırlama (Duygu Etiketli)
        tts_segments = []
        for seg in segments:
            tag = str(seg.get('tts_tag', '') or '').strip()
            text = str(seg.get('text', '') or '').strip()
            if not text:
                continue
            tts_text = f"{tag} {text}".strip() if tag else text
            
            if mod == "SOLO_FEMALE":
                tts_segments.append({"speaker": "female", "text": tts_text})
            elif mod == "SOLO_MALE":
                tts_segments.append({"speaker": "male", "text": tts_text})
            else:
                speaker = str(seg.get("speaker") or "female").strip().lower()
                tts_segments.append({"speaker": speaker if speaker in {"female", "male"} else "female", "text": tts_text})
            
        if soru:
            last_speaker = tts_segments[-1].get("speaker", "female") if tts_segments else "female"
            final_speaker = "male" if last_speaker == "female" else "female"
            
            if mod == "SOLO_FEMALE":
                final_speaker = "female"
            elif mod == "SOLO_MALE":
                final_speaker = "male"
                
            tts_segments.append({"speaker": final_speaker, "text": f"[vurgulu] {soru}"})

        duo_script = {
            "status": "ready" if tts_segments else "fallback",
            "segments": tts_segments,
            "contract": {"mode": mod},
            "conversation_design": {},
            "model": "agentic"
        }
        
        duo_plan = {"mode": mod, "target_words": hedef}
        son_reels, son_duo_plan, son_duo_script = reels_state, duo_plan, duo_script
        
        if not tts_segments:
            if deneme < VOICE_REGEN_MAX:
                hedef_kelime_bilgisi = f"Önceki üretimde konuşma segmenti yoktu. Hedef {hedef}, izin verilen {minimum}-{maksimum} kelime; segments alanını mutlaka doldur."
                log(f'⚠️ Script Writer boş senaryo döndürdü; yeniden yazılıyor ({deneme+1}/{VOICE_REGEN_MAX}).')
                continue
            log('❌ Script Writer geçerli senaryo üretemedi.')
            break
        
        # Kelime Kontrolü
        adet = _kelime_sayisi(full_text)
        log(f'📝 Agentic Seslendirme uzunluk kontrolü: {adet} kelime | hedef {hedef} | izin verilen {minimum}-{maksimum}')
        
        if adet < minimum or adet > maksimum:
            sapma = abs(adet - hedef) / float(hedef or 1)
            if sapma <= VOICE_WORD_TOLERANCE_RATIO:
                log(f'🎚️ Kelime sayısı aralık dışında ama tolerans içinde (sapma %{sapma*100:.0f} ≤ %{VOICE_WORD_TOLERANCE_RATIO*100:.0f}); yeniden yazım atlandı, video senkron katmanı dengeleyecek.')
            elif deneme < VOICE_REGEN_MAX:
                hedef_kelime_bilgisi = f"Önceki üretim {adet} kelimeydi. Hedef {hedef}, izin verilen {minimum}-{maksimum}. Bu kez metni mutlaka bu aralıkta tut."
                log(f'⚠️ Kelime aralığı dışında (sapma %{sapma*100:.0f}); Script Writer yeniden yazıyor ({deneme+1}/{VOICE_REGEN_MAX}).')
                continue
            else:
                log('⚠️ Kelime aralığı hala düzeltilemedi, devam ediliyor.')
        
        # TTS Üretimi
        ses_dosyasi = gecici_ses_yolu()
        ok, info, mod_tts = _duo_ses_veya_legacy_uret(router, duo_script, full_text, legacy_voice, log, ses_dosyasi)
        
        if not ok:
            temp_dosya_temizle(ses_dosyasi)
            if deneme < VOICE_REGEN_MAX:
                hedef_kelime_bilgisi = "TTS üretimi başarısız oldu. Daha doğal ve okunabilir bir metin yaz."
                if mod == "DUO":
                    hedef_kelime_bilgisi += " DUO modunda hem female hem male konuşmacı mutlaka yer almalı."
                continue
            return reels_state, "hata", duo_plan, duo_script, False, None, "LEGACY", "", {}
            
        uyumlu, ses_suresi, oran = _ses_sure_uyumlu_mu(ses_dosyasi, sure_saniye)
        log(f'🎚️ Agentic TTS gerçek süre kontrolü: video {sure_saniye:.2f}s → ses {ses_suresi:.2f}s | oran {oran:.2f}x')
        
        if os.path.exists(ses_dosyasi) and ses_suresi > 0:
            if not uyumlu:
                # Geçerli WAV süre oranı yüzünden ÇÖPE ATILMAZ: her yeniden üretim
                # Script + Critic + TTS API çağrısı demektir ve 503 yoğunluğunda
                # üretimi durduruyordu. FFmpeg senkron katmanı (0.5x-1.5x video
                # hızı) farkı zaten kapatıyor.
                log(f'🎚️ TTS/video oranı {oran:.2f}x; geçerli WAV korunuyor, video senkron katmanına bırakılıyor.')
            
            # Başarılı TTS ve Süre. Metadata üretilir ve döngüden çıkılır.
            # (Atlanırsa captin, _qa_regeneration_loop içindeki Caption ajanı ile tamamlanır.)
            metadata_state, _ = _istege_bagli_ajan(
                log, "🏷️ Metadata Generator Ajan",
                lambda: _metadata_gen_calistir(router, script_state, hook_state, fact_state, log),
                {},
                router=router,
                atlanabilir=True,
            )
            reels_state["metadata"] = metadata_state
            
            return reels_state, "agentic", duo_plan, duo_script, True, info, mod_tts, ses_dosyasi, metadata_state

        temp_dosya_temizle(ses_dosyasi)

    return son_reels, son_model, son_duo_plan, son_duo_script, False, None, "LEGACY", "", {}


def _qa_calistir(router,video_state,fact_state,editorial_state,reels_state,caption_state,threads_state,sure_saniye,log,duo_plan=None,duo_script=None,ton=None):
    content=girdi_birlestir(durumu_metne_donustur('VIDEO',video_state),durumu_metne_donustur('FACT LOCK',fact_state),durumu_metne_donustur('EDITORIAL',editorial_state),durumu_metne_donustur('REELS',reels_state),durumu_metne_donustur('DUO PLAN',duo_plan or {}),durumu_metne_donustur('DUO SCRIPT',duo_script or {}),durumu_metne_donustur('CAPTION',caption_state),durumu_metne_donustur('THREADS',threads_state),f'VIDEO SÜRESİ: {sure_saniye}',f'SEÇİLEN İÇERİK TÜRÜ: {ton or "dengeli"}')
    try:
        with _istek_profili(router, "uzun_metin"):
            result, model = _run_timed(
                log, "Final QA (Gemini)",
                lambda: router.metin_uret(content,qa_promptunu_olustur(ton),QA_SCHEMA,log,arama_kullan=False),
            )
    except Exception as exc:
        # QA API'si (tüm model+key + aşırı-yük tekrarları) geçici olarak
        # erişilemezse hazır TTS/render çöpe atılmaz: yapısal kontroller
        # (_qa_regeneration_loop içindeki DUO/TTS dosyası doğrulaması) yine
        # uygulanır; model QA'sı atlandığı açıkça işaretlenir.
        log(f"⚠️ Final QA modeli geçici olarak erişilemedi; yapısal kontrollerle devam ediliyor: {type(exc).__name__}: {str(exc)[:160]}")
        return {"overall": "PASS", "regeneration_targets": [], "qa_unavailable": True, "reason": str(exc)[:200]}, "qa-unavailable"
    result = _object_state_or_empty(result)
    if not result:
        result = {"overall":"FAIL","regeneration_targets":["QA_PARSE_FAIL"]}
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
    model_caption = "agentic"
    caption_state, model_caption = _caption_eksikse_tamamla(router, caption_state, model_caption, reels_state, fact_state, editorial_state, video_state, log, ton)
    
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
                return reels_state,caption_state,threads_state,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi,qa_state,qa_rounds,model_reels,model_caption,model_threads,True

        if not supported_targets:
            break
        if qa_round >= MAX_QA_REGEN:
            break

        target_set=set(supported_targets)
        creative_needed=bool(target_set & {'VOICEOVER_FAIL','COVER_FAIL', 'DUO_SCRIPT_FAIL'})
        downstream_threads='THREADS_FAIL' in target_set
        caption_only='CAPTION_FAIL' in target_set and not creative_needed
        
        log(f"⚠️ QA başarısız bulundu ({', '.join(supported_targets)}). İlgili agentic katmanlar yeniden üretiliyor...")
        
        if creative_needed:
            onceki = (reels_state,model_reels,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi,caption_state,model_caption)
            try:
                reels_state,model_reels,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi, metadata_state = agentic_icerik_uretimi(
                    router,video_state,fact_state,editorial_state,sure_saniye,ton,legacy_voice,log, mod_karari=mod_karari
                )
                caption_state = _caption_state_normalize(metadata_state)
                model_caption = "agentic"
                caption_state, model_caption = _caption_eksikse_tamamla(router, caption_state, model_caption, reels_state, fact_state, editorial_state, video_state, log, ton)
            except Exception as exc:
                # QA yenilemesi API yoğunluğunda düşerse elde kalan geçerli
                # üretim çöpe atılmaz; önceki sürüm korunur.
                log(f"⚠️ QA yenilemesi üretilemedi; önceki agentic çıktı korunuyor: {type(exc).__name__}: {str(exc)[:160]}")
                reels_state,model_reels,duo_plan,duo_script,ses_basarili,kullanilan_ses_modeli,ses_modu,ses_dosyasi,caption_state,model_caption = onceki
        elif caption_only:
            try:
                caption_state, model_caption = _caption_calistir(router,reels_state,fact_state,editorial_state,video_state,log,ton)
            except Exception:
                pass
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
