"""Agentic 4 Ajanlı Viral Üretim Döngüsü.

Sıra: Detective → Hook Generator → Script Writer (zorunlu) → Critic → TTS → Metadata.
Detective / Hook / Critic / Metadata isteğe bağlıdır: API'si geçici olarak tamamen
düşerse güvenli varsayılanla devam edilir, pipeline ölmez. Script Writer zorunludur;
router'ın aşırı-yük tekrarlarına rağmen düşerse hata yukarı taşınır.

Bu modül `core.pipeline`'ı İTHAL ETMEZ (pipeline bu modülden agentic_icerik_uretimi
ve ortak yardımcılarını alır); import döngüsü oluşmaz.
"""
import json
import os
import re
import time
from contextlib import contextmanager

from core.config import KELIME_HIZI_ORANI, SES_HIZ_CARPANI
from core.schemas import (
    DETECTIVE_SCHEMA, HOOK_GEN_SCHEMA, SCRIPT_WRITER_SCHEMA,
    CRITIC_SCHEMA, METADATA_GEN_SCHEMA,
)
from core.prompts import durumu_metne_donustur, girdi_birlestir
from core.media import (
    _ses_suresini_al, gecici_ses_yolu, temp_dosya_temizle,
)
from core.cover_titles import (
    ALTERNATIF_SAYISI as KAPAK_ALTERNATIF_SAYISI,
    kapak_basliklarini_normalize_et,
    kapak_basliklarini_metne_dok,
)
from core.duo_audio import duo_ses_uret

# --- TTS / kelime güvenli limitleri -----------------------------------------
VOICE_REGEN_MAX = 2
VOICE_DURATION_MIN_RATIO = 0.85
VOICE_DURATION_MAX_RATIO = 1.15
# Kelime aralığı dışındaki senaryo yalnızca sapma bu oranın ÜZERİNDEYSE yeniden
# yazdırılır (hedefe göre). Küçük sapmayı FFmpeg senkron katmanı (0.5x-1.5x video
# hızı) zaten kapatıyor; tam Script+Critic turu API yoğunluğunda pahalıdır.
VOICE_WORD_TOLERANCE_RATIO = 0.20


def _safe_result_summary(value):
    """Log model çıktısının içeriğini değil yalnızca güvenli yapısal özetini."""
    state = value[0] if isinstance(value, tuple) and value else value
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
    model = value[1] if isinstance(value, tuple) and len(value) > 1 else None
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


# --- Router bağlam yardımcıları (pipeline tarafından da paylaşılır) ----------

@contextmanager
def _hizli_basarislik(router):
    """Güvenli varsayılanı olan adımlarda router'ın aşırı-yük beklemesini kapatır;
    bekleme bütçesi zorunlu adımlara (Script Writer, TTS, Forensic...) kalır."""
    ctx = getattr(router, "hizli_basarislik", None)
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
    denenmez (logda Detective 77 sn, Critic 236 sn boşa harcandı); güvenli
    varsayılanla anında devam edilir."""
    if atlanabilir and _yakin_zamanda_asiri_yuk(router):
        log(f"⏭️ {etiket} atlandı: API yakın zamanda tam tur aşırı yük verdi; güvenli varsayılanla devam ediliyor.")
        return varsayilan, "atlandi"
    try:
        with _hizli_basarislik(router), _istek_profili(router, "istege_bagli"):
            state, model = callback()
    except Exception as exc:
        log(f"⚠️ {etiket} kullanılamadı; güvenli varsayılanla devam ediliyor: {type(exc).__name__}: {str(exc)[:160]}")
        return varsayilan, "hata"
    return _object_state_or_empty(state), model


def _object_state_or_empty(value):
    """dict / JSON-string state'leri güvenli dict'e indirger (pipeline da kullanır)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value.strip())
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _kelime_sayisi(metin):
    return len(re.findall(r"\b[\wÇĞİÖŞÜçğıöşüÀ-ÿ]+(?:[-'][\wÇĞİÖŞÜçğıöşüÀ-ÿ]+)*\b", str(metin or ""), re.UNICODE))


def _reels_kelime_ayarlarini_hazirla(sure_saniye, kelime_hizi_orani=None):
    """Hedef/minimum/maksimum kelime + oran + yuvarlama birimlerini hesaplar."""
    oran = float(kelime_hizi_orani or KELIME_HIZI_ORANI)
    yuvarlama = 5
    hedef = max(5, int(round((float(sure_saniye or 30) * oran) / yuvarlama) * yuvarlama))
    minimum = max(5, int(round(hedef * 0.90)))
    maksimum = max(minimum, int(round(hedef * 1.10)))
    return hedef, minimum, maksimum, oran, yuvarlama


# --- Ses modu yardımcıları ----------------------------------------------------

def _mod_icin_legacy_ses(mode, default_voice='Autonoe'):
    if mode == 'SOLO_FEMALE':
        return 'Autonoe'
    if mode == 'SOLO_MALE':
        return 'Charon'
    return default_voice or 'Autonoe'


def _beklenen_gercek_mod(duo_plan, duo_script):
    """Script sözleşmesinin taşıdığı (varsa) modu, plan üzerinden doğrular."""
    mode = str(((duo_script or {}).get("contract", {}) or {}).get("mode") or (duo_plan or {}).get("mode") or "DUO").strip().upper()
    return mode if mode in {"DUO", "SOLO_FEMALE", "SOLO_MALE"} else "DUO"


def _duo_ses_veya_legacy_uret(router, duo_script, legacy_text, legacy_voice, log, output_path):
    """DUO modda fail-closed multi-speaker TTS; SOLO modda tek prebuilt voice TTS."""
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
    """TTS/video süre oranı tanısı (diagnostik; oran dışı WAV silinmez, FFmpeg'e bırakılır)."""
    ses_suresi = _ses_suresini_al(ses_dosyasi) if ses_dosyasi and os.path.exists(ses_dosyasi) else 0.0
    video_suresi = _coerce_positive_float(video_suresi)
    if video_suresi is None or ses_suresi <= 0:
        return False, ses_suresi, 0.0
    oran = ses_suresi / video_suresi
    return VOICE_DURATION_MIN_RATIO <= oran <= VOICE_DURATION_MAX_RATIO, ses_suresi, oran


# --- 4 ajan çalıştırıcıları ----------------------------------------------------

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
        diyalog_kurallari = (
            "DUO DİYALOG OMURGASI: HOOK → FRICTION → PROOF → REVERSAL → PAYOFF/CALLBACK kur; ilk iki tur mümkünse farklı konuşmacılardan gelsin.\n"
            "LEXİCAL UPTAKE: 2. turdan itibaren repliklerin çoğu önceki replikteki somut bir iddia, rakam veya kelimeyi yakalasın.\n"
            "ASİMETRİK RİTİM: Replik uzunlukları eşit olmasın; mekanik kadın-erkek-kadın salınımı, röportaj tipi soru-cevap ve aynı fikrin tekrarı YASAK.\n"
            "Kapanışta açılıştaki kelime/fikre callback yap; iki bağımsız monolog veya şarkıcı düeti hissi olmasın."
        )
    elif mod == "SOLO_FEMALE":
        karakter_bilgisi = "Karakter: Sadece Autonoe (şüpheci, zeki kadın). Tek kişilik monolog."
        diyalog_kurallari = "Akış: hook → bilgi → dönüş → callback. Diyalog kalıbı, ikinci karaktere hitap veya soru-cevap boşluğu YASAK."
    else:
        karakter_bilgisi = "Karakter: Sadece Charon (iddialı, kanıtlayan erkek). Tek kişilik monolog."
        diyalog_kurallari = "Akış: hook → bilgi → dönüş → callback. Diyalog kalıbı, ikinci karaktere hitap veya soru-cevap boşluğu YASAK."

    prompt = (
        f"Sen otoXtra'nın Sohbet Yazarısın. {karakter_bilgisi}\n"
        "Kanca ve Dedektif verilerini kullanarak doğal bir anlatım yaz.\n"
        f"{diyalog_kurallari}\n"
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


def _turkiye_ilgi_kancasi_getir(fact_state):
    """Fact Lock'taki en yüksek önem puanlı Türkiye ilgi sinyalini güvenli anlatımla döndürür."""
    sinyaller = (fact_state or {}).get("turkiye_ilgi_sinyalleri") if isinstance(fact_state, dict) else []
    if not isinstance(sinyaller, list):
        return ""
    en_iyi = None
    for s in sinyaller:
        if not isinstance(s, dict):
            continue
        try:
            puan = float(s.get("onem_puani") or 0)
        except (TypeError, ValueError):
            puan = 0.0
        if en_iyi is None or puan > en_iyi[0]:
            en_iyi = (puan, s)
    if en_iyi is None:
        return ""
    return str(en_iyi[1].get("guvenli_anlatim") or en_iyi[1].get("bulgu") or "").strip()


# --- Ana döngü -----------------------------------------------------------------

def agentic_icerik_uretimi(router, video_state, fact_state, editorial_state, sure_saniye, ton, legacy_voice, log, mod_karari=None):
    """4 Ajanlı Viral Üretim Döngüsü (Kelime ve TTS Süre Güvenlik Duvarlı).

    Dönüş (9'luplü):
      (reels_state, model, duo_plan, duo_script, ses_basarili,
       kullanilan_ses_modeli, ses_modu, ses_dosyasi, metadata_state)
    """
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
    turkiye_kancasi = _turkiye_ilgi_kancasi_getir(fact_state)

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
            "turkiye_ilgi_kancasi": turkiye_kancasi,
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
                # Script + Critic + TTS API çağrısı demektir. FFmpeg senkron
                # katmanı (0.5x-1.5x video hızı) farkı zaten kapatıyor.
                log(f'🎚️ TTS/video oranı {oran:.2f}x; geçerli WAV korunuyor, video senkron katmanına bırakılıyor.')

            # Başarılı TTS ve Süre. Metadata üretilir ve döngüden çıkılır.
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
