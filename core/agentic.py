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
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import core.config as _cfg
from core.config import KELIME_HIZI_ORANI, SES_HIZ_CARPANI
from core.schemas import (
    DETECTIVE_SCHEMA, HOOK_GEN_SCHEMA, SCRIPT_WRITER_SCHEMA,
    CRITIC_SCHEMA, METADATA_GEN_SCHEMA,
)
from core.prompts import durumu_metne_donustur, girdi_birlestir, metadata_promptunu_olustur
from core.media import (
    _ses_suresini_al, gecici_ses_yolu, temp_dosya_temizle,
)
from core.cover_titles import (
    ALTERNATIF_SAYISI as KAPAK_ALTERNATIF_SAYISI,
    kapak_basliklarini_normalize_et,
    kapak_basliklarini_metne_dok,
)
from core.duo_audio import duo_ses_uret
from core.tts_delivery import replik_tts_hazirla, script_metnini_konusmaya_cek, segment_konusma, teslimat_blogu
from core.otv_kilidi import (
    kapaklari_otv_kilidine_cek,
    metni_otv_kilidine_cek,
    senaryoyu_otv_kilidine_cek,
    sosyal_metni_otv_kilidine_cek,
    vergi_kilidi_talimati,
)


def _metni_kilide_cek(metin, fact_state):
    kilit = (fact_state or {}).get("vergi_kilidi") if isinstance(fact_state, dict) else None
    if not isinstance(kilit, dict):
        return str(metin or "")
    return metni_otv_kilidine_cek(metin, kilit, "")

# --- TTS / kelime güvenli limitleri -----------------------------------------
VOICE_REGEN_MAX = 2
VOICE_DURATION_MIN_RATIO = 0.85
VOICE_DURATION_MAX_RATIO = 1.15
# Kelime aralığı dışındaki senaryo yalnızca sapma bu oranın ÜZERİNDEYSE yeniden
# yazdırılır (hedefe göre). Küçük sapmayı FFmpeg senkron katmanı (0.5x-1.5x video
# hızı) zaten kapatıyor; tam Script+Critic turu API yoğunluğunda pahalıdır.
VOICE_WORD_TOLERANCE_RATIO = 0.20

# --- Cümle-tabanlı uzunluk bütçesi (Eylül 2026, 2. tur) ----------------------
# Flash serisi TOPLAM kelime sayısını tutturamaz (355 hedef → 155/150/160
# üretti) ama liste elemanı / cümle SAYABİLİR. Hedef kelime, ortalama Türkçe
# cümle uzunluğuna bölünüp "TAM N cümle yaz" olarak verilir; kod tarafındaki
# deterministik kelime kontrolü nihai ölçü olmaya devam eder.
CUMLE_KELIME_ORTALAMA = 11
CUMLE_KELIME_MIN = 9
CUMLE_KELIME_MAX = 13


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
    ctx = getattr(router, "hizli_basarislik", None) or getattr(router, "hizli_basarisizlik", None)
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


@contextmanager
def _istege_bagli_router_baglami(router):
    """İsteğe bağlı ajan istekleri için router bağlamı.

    KALİTE ÖNCELİKLİ (varsayılan): `istege_bagli` profili (kısa zaman aşımı +
    süre bütçesi) uygulanır ama aşırı-yük tekrar denemesi KAPATILMAZ; ajan
    yoğunlukta 15/30 sn bekleyip yeniden dener. Beklemeler router'da zorunlu
    adımlardan AYRI bir havuzdan düşer. `ISTEGE_BAGLI_HIZLI_BASARISIZLIK=1`
    ile eski hız-öncelikli davranış (tek tam tur, beklemesiz) açılabilir."""
    if getattr(_cfg, "ISTEGE_BAGLI_HIZLI_BASARISIZLIK", False):
        with _hizli_basarislik(router), _istek_profili(router, "istege_bagli"):
            yield
    else:
        with _istek_profili(router, "istege_bagli"):
            yield


def _asiri_yukte_atla_mi(router, atlanabilir=True):
    """Yakın zamandaki tam-tur aşırı yükte isteğe bağlı çağrı hiç yapılmasın mı?
    Varsayılan HAYIR (kalite): ajan her zaman denenir. Yalnız
    `ISTEGE_BAGLI_ASIRI_YUK_ATLA=1` operasyon anahtarıyla eski atlama açılır."""
    return bool(atlanabilir and getattr(_cfg, "ISTEGE_BAGLI_ASIRI_YUK_ATLA", False) and _yakin_zamanda_asiri_yuk(router))


def _istege_bagli_ajan(log, etiket, callback, varsayilan, router=None, atlanabilir=False):
    """Zenginleştirici ajan (Detective/Hook/Critic/Metadata) API'si geçici olarak
    tamamen düşerse tüm pipeline'ı öldürmek yerine güvenli varsayılanla devam eder.

    Varsayılan politika kalite önceliklidir: ajan her zaman denenir ve router'ın
    aşırı-yük tekrarlarından (ayrı bekleme havuzuyla) yararlanır. Asılı kalan
    istekler `istege_bagli` profilinin zaman aşımı/bütçesiyle sınırlıdır.
    atlanabilir=True: yalnız `ISTEGE_BAGLI_ASIRI_YUK_ATLA=1` iken, yakın zamanda
    tam-tur aşırı yük görüldüyse ajan denenmeden varsayılana düşer."""
    if _asiri_yukte_atla_mi(router, atlanabilir):
        log(f"⏭️ {etiket} atlandı: API yakın zamanda tam tur aşırı yük verdi; güvenli varsayılanla devam ediliyor.")
        return varsayilan, "atlandi"
    try:
        with _istege_bagli_router_baglami(router):
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


def _senaryo_metni(script_state):
    """Senaryonun seslendirilecek tamamı: tüm segment metinleri + kapanış sorusu."""
    segments = [seg for seg in ((script_state or {}).get("segments") or []) if isinstance(seg, dict)]
    metin = " ".join(str(seg.get("text", "") or "").strip() for seg in segments)
    soru = str((script_state or {}).get("yorum_tetikleyici_soru") or "").strip()
    return f"{metin} {soru}".strip()


def _segment_butcesi_olustur(hedef, minimum, maksimum, mod):
    """Modelin 'kısaca özetle' refleksini kıran somut yapısal bütçe — CÜMLE TABANLI.
    Kelime hedefini tek tek saymak flash serisinin yapamadığı iştir; cümle
    sayabildiği için hedef kelime → cümle hedefine çevrilir (TAM N cümle +
    cümle başına kelime aralığı). Replik sayısı cümle sayısından türetilir.
    Nihai ölçü kod tarafındaki deterministik kelime kontrolüdür (değişmez)."""
    try:
        hedef = int(hedef or 0)
        minimum = int(minimum or 0)
        maksimum = int(maksimum or 0)
    except (TypeError, ValueError):
        hedef, minimum, maksimum = 0, 0, 0
    if hedef <= 0:
        hedef, minimum, maksimum = 60, 54, 66
    hedef_cumle = max(4, round(hedef / CUMLE_KELIME_ORTALAMA))
    # Kabul aralığı kelime aralığının cümle karşılığı: en uzun cümle varsayımı
    # alt sınırı, en kısa cümle varsayımı üst sınırı verir.
    cumle_min = max(3, round(minimum / (CUMLE_KELIME_MAX + 1)))
    cumle_max = max(hedef_cumle, round(maksimum / CUMLE_KELIME_MIN))
    replik_min = max(4, -(-hedef_cumle // 2))
    replik_max = max(replik_min + 1, hedef_cumle)
    sonu = " DUO modunda iki konuşmacının cümleleri BİRLİKTE bu bütçeyi oluşturur." if mod == "DUO" else ""
    return (
        f"🔢 SÜRE BÜTÇESİ (CÜMLE TABANLI): Seslendirme metnini TAM {hedef_cumle} CÜMLE yaz; kabul aralığı {cumle_min}-{cumle_max} cümle. "
        f"Her cümle sohbet tempolu, {CUMLE_KELIME_MIN}-{CUMLE_KELIME_MAX} kelime; 18+ kelimelik cümle ve 'Evet', 'Doğru' gibi boşluk cümleleri YASAK. "
        f"Cümleleri TEK TEK SAY (1, 2, 3, ...): hedefe ulaşmadan bitirmek YASAK. "
        f"Cümleleri {replik_min}-{replik_max} repliğe böl; her replik 1-2 cümle ve her replik yeni bilgi, kanıt veya reaksiyon taşımalı.{sonu} "
        "Kelime hedefi bu bütçeden doğar: cümle sayısını yakalarsan kelime hedefi kendiliğinden gelir."
    )


def _metni_normalizle(metin):
    """Segment/soru karşılaştırma normalizasyonu: TTS etiketlerini atar,
    boşlukları sıkıştırır, casefold eder ve uçlardaki noktalamayı temizler."""
    m = re.sub(r"\[[^\]]*\]", " ", str(metin or ""))
    m = re.sub(r"\s+", " ", m).strip().casefold()
    return m.strip(" .,!?;:…-")


def _uzatma_gecerli_mi(eski_metin, yeni_metin, eski_adet, yeni_adet, eski_parcalar=None):
    """Uzatma çıktısını kabul etme koşulları:
    1) Yeni metin eskisinden UZUN olmalı (uzatmanın tek işlevi kelime kazandırmak).
    2) Açılış korunmalı: eski senaryonun (TTS etiketsiz, normalizasyonlu) ilk
       60 karakteri yeni metinde aynen geçmeli. Model yalnızca ekteleri değil
       TAM senaryoyu döndürmek zorunda; yoksa kapağa gömülü hook açılışı kaybolur.
    3) eski_parcalar verildiyse: eski repliklerin HER BİRİ (normalizasyonlu metniyle)
       yeni metinde AYNI SIRADA yer almalı. Model açılışı korusa bile ortadaki
       replikleri yeniden yazarak "kopuk ikinci senaryo" üretemez."""
    if yeni_adet <= eski_adet:
        return False
    eski, yeni = _metni_normalizle(eski_metin), _metni_normalizle(yeni_metin)
    if not eski:
        return True
    onecik = eski[:60].rstrip()
    if not onecik or onecik not in yeni:
        return False
    if eski_parcalar:
        poz = 0
        for parca in eski_parcalar:
            p = _metni_normalizle(parca)
            if not p:
                continue
            idx = yeni.find(p, poz)
            if idx < 0:
                return False
            poz = idx + len(p)
    return True


# Dolgu edat/edek/kısaltmalar: token örtüşme hesabında anlam taşımaz; filtre
# edilmezse kısa repliklerde sahte yüksek Jaccard verir.
_TEKRAR_DURAK = frozenset({
    "ve", "ile", "de", "da", "ki", "mi", "mı", "mu", "mü", "bir", "bu", "şu",
    "o", "ise", "ama", "veya", "her", "ne", "çok", "daha", "en", "için",
    "gibi", "şimdi", "aslında", "bence", "sana", "ben", "evet", "hayır",
    "hala", "hâlâ", "diye", "yani", "böyle",
})


def _anlami_tokenlar(metin):
    m = re.sub(r"\[[^\]]*\]", " ", str(metin or ""))
    return {
        t for t in re.findall(r"[0-9a-zçğıöşü]+", m.casefold())
        if (len(t) > 2 or t.isdigit()) and t not in _TEKRAR_DURAK
    }


def _tekrarli_replik_var_mi(eski_segments, yeni_segments, esik=0.6):
    """Yeni repliklerden biri eski repliklerden (veya diğer yeni repliklerden)
    biriyle eşik kadar token paylaşıyorsa MÜKERRER ANLATICILIK (aynı fikrin
    kelimeler değiştirilip tekrar anlatılması) sayılır. Flash modeller 'uzat'
    derken mevcut replikleri cümle çevirerek tekrar ettirir; prompt uyarısı
    bunu tam durduramaz → deterministik koruma. Token Jaccard kullanılır:
    gerçek tekrar ~0.7+, 'aynı rakamı referans alma' (callback) ~0.2."""
    yeniler = [
        _anlami_tokenlar(seg.get("text"))
        for seg in (yeni_segments or [])
        if isinstance(seg, dict) and str(seg.get("text") or "").strip()
    ]
    eskiler = [
        _anlami_tokenlar(seg.get("text"))
        for seg in (eski_segments or [])
        if isinstance(seg, dict) and str(seg.get("text") or "").strip()
    ]
    for i, t in enumerate(yeniler):
        if not t:
            continue
        for esk in eskiler:
            if esk and len(t & esk) / max(1, len(t | esk)) >= esik:
                return True
        for j in range(i + 1, len(yeniler)):
            if yeniler[j] and len(t & yeniler[j]) / max(1, len(t | yeniler[j])) >= esik:
                return True
    return False


def _ek_replikler_bul(eski_segments, yeni_segments):
    """Tam senaryo çıktısındaki EKLENTİ replikler: eski repliklerin birebir
    (normalizasyonlu) kopyaları düşüldükten sonra kalanlar. Mükerrerlik
    kontrolü yalnız eklentilere uygulanır — korunan eski replikler 'kendisiyle
    aynı' sayılır; bu hata değil, uzatma sözleşmesinin ta kendisidir."""
    kalan = [seg for seg in (yeni_segments or []) if isinstance(seg, dict)]
    for eski_seg in (eski_segments or []):
        if not isinstance(eski_seg, dict):
            continue
        hedef = _metni_normalizle(eski_seg.get("text"))
        if not hedef:
            continue
        for idx, seg in enumerate(kalan):
            if _metni_normalizle(seg.get("text")) == hedef:
                del kalan[idx]
                break
    return kalan


def _kapanis_sorusunu_ayristir(script_state, log=None):
    """Kapanış sorusu REPLİK olarak segments'te duruyorsa çıkarır.

    Soru `yorum_tetikleyici_soru` alanındadır ve sistem seslendirme metninin
    EN SONUNA ekler. Replik listesinde de duruyorsa (a) uzatmada yeni replikler
    ondan sonra geldiği için soru metnin ORTASINA gömülür, (b) seslendirme soruyu
    iki kez okur. Model bu alanı AYNEN korumaya zorlandığı için replik kopyası
    güvenle atılabilir; akışta kapanış her zaman (tek sefer) soruyla biter."""
    if not isinstance(script_state, dict) or not script_state:
        return script_state or {}
    soru = _metni_normalizle(script_state.get("yorum_tetikleyici_soru"))
    if not soru:
        return script_state
    segments = [seg for seg in (script_state.get("segments") or []) if isinstance(seg, dict)]
    if not segments or _metni_normalizle(segments[-1].get("text")) != soru:
        return script_state
    yeni = dict(script_state)
    yeni["segments"] = segments[:-1]
    if log:
        log("🧹 Kapanış sorusu replik listesinden çıkarıldı; sistem soruyu seslendirmenin en sonuna ekleyecek.")
    return yeni


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

    parcalar = []
    for seg in (duo_script or {}).get("segments") or []:
        konusma, _stil = segment_konusma(seg)
        if konusma:
            parcalar.append(konusma)
    metin = " ".join(parcalar) or legacy_text
    teslimat = teslimat_blogu((duo_script or {}).get("segments") or [])
    ok, info = _run_timed(
        log, "Legacy tek ses TTS + WAV hazırlama",
        lambda: router.ses_uret(metin, effective_legacy_voice, output_path, log, hiz_carpani=SES_HIZ_CARPANI, teslimat=teslimat),
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
        + vergi_kilidi_talimati(fact_state)
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

# Kalite çubuğu (Eylül 2026: şablon odaklı prompt genel geçer, somutsuz başlık
# üretiyordu: 'ARTIK HİÇBİR ŞEY AYNI' / 'Tüm dengeler değişiyor'). Somut dayanak
# zorunlu; genel geçer formüller yasak.
KAPAK_KALITE_KURALI = (
    "🚨 [Kural: Kapak Başlığı Kalite Çubuğu] — İSTİSNASIZ:\n"
    "- SOMUT DAYANAK ZORUNLU: En az 3 alternatiften biri (tercihen 1. sıra) Fact Lock / Detective verisindeki "
    "SOMUT bir unsuru içersin: rakam, fiyat, model adı, ÖTV/vergi, pazar, nesil, ölçülebilir fark. "
    "Somut isimsiz 'şok/devrim/krallık/denge değişti' dili YASAK.\n"
    "- GENEL GEÇER KALİPLER YASAK: 'ARTIK HİÇBİR ŞEY AYNI', 'TAMAMEN DEVRİM', 'BÜYÜK ŞOK', 'HERKES YANILIYOR' "
    "gibi boş iddialar; alt başlıkta bile 'tüm dengeler değişiyor' gibi hiçbir şey söylemeyen cümleler yazma.\n"
    "- HER ALT BAŞLIK BİR SEBEP VERSİN: 'Neden izleyeceksin' sorusuna somut ipucu ver (cevabı değil); "
    "kurmaca süsleme ve abartılı sıfat yığını (devasa, tarihin, efsanevi) kullanma.\n"
    "- 1. SIRA KULLANILACAK KAPAKTIR: 5 alternatifin EN GÜÇLÜ ve EN SOMUT olanı 1. sıraya yazılmalı; "
    "kalitesi düşen alternatifleri son sıraya koy.\n"
    "- ÖRNEK (iyi): ust='ALMAN TEKELİ BİTİYOR' / alt='Lüks sedana 10 yıldır ilk ciddi hamle' — somut pazar + ölçülebilir iddia.\n"
    "- ÖRNEK (kötü): ust='ARTIK HİÇBİR ŞEY AYNI' / alt='Tüm dengeler değişiyor' — somut dayanak yok, kullanılamaz."
)


def _hook_gen_calistir(router, detective_state, fact_state, editorial_state, log, geri_bildirim=""):
    prompt = (
        "Sen otoXtra'nın Kışkırtıcı Kanca Üreticisisin. Dedektifin bulduğu malzemeyi kullanarak "
        "ilk 3 saniyede izleyiciyi şok edecek bir kanca üreteceksin.\n"
        "Şablonlardan birini seç: Efsane_Curutme, Ters_Kose, Negatif_Uyari.\n"
        "Şablon FORMÜLDÜR; metin somut dayanakla (rakam, model, pazar, vergi) doldurulur — formülün kendisini basma.\n"
        "Kapak metni ve ilk 3 saniye kancası ultra viral ve iddialı olmalı; ilk 3 saniye kancası kapak başlığını birebir tekrar etmemeli.\n\n"
        + KAPAK_FORMAT_KURALI
        + "\n\n"
        + KAPAK_KALITE_KURALI
        + vergi_kilidi_talimati(fact_state)
    )
    if geri_bildirim:
        prompt += f"\n\n🚨 FINAL QA GERİ BİLDİRİMİ (kapak/kancayı buna göre düzelt): {geri_bildirim}"
    content = girdi_birlestir(
        durumu_metne_donustur('DETECTIVE', detective_state),
        durumu_metne_donustur('FACT LOCK', fact_state),
        durumu_metne_donustur('EDITORIAL', editorial_state)
    )
    return _run_timed(
        log, "🪝 Hook Generator Ajan (Kanca Üretimi)",
        lambda: router.metin_uret(content, prompt, HOOK_GEN_SCHEMA, log, arama_kullan=False),
    )


def _script_writer_calistir(router, hook_state, detective_state, fact_state, editorial_state, video_state, sure_saniye, log, feedback="", hedef_kelime_bilgisi="", mod="DUO", qa_geri_bildirimi="", hedef_kelime=0, segment_butcesi="", mevcut_script=None, mevcut_kelime=0, eksik_kelime=0):
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

    if mevcut_script is not None:
        # UZATMA MODU: model sıfırdan "daha uzun yaz" derken kısacık senaryo
        # üretmeye devam ediyordu (155→150→160 kelime). Uzatma yerel ve somut
        # bir görevdir: mevcut metni birebir koru, eksik kelimeleri yeni replik
        # olarak ekle. (Eylül 2026 üretim logundaki kök neden.)
        eski_segments = [s for s in (mevcut_script.get("segments") or []) if isinstance(s, dict) and str(s.get("text", "") or "").strip()]
        eski_soru = str(mevcut_script.get("yorum_tetikleyici_soru") or "").strip()
        satirlar = []
        for i, seg in enumerate(eski_segments, 1):
            sp = str(seg.get("speaker") or "?").strip()
            tag = str(seg.get("tts_tag") or "").strip()
            metin = str(seg.get("text") or "").strip()
            satirlar.append(f"{i}. [{sp}] {tag} {metin}".rstrip())
        mevcut_metin = "\n".join(satirlar) or "(boş)"
        eksik = max(1, int(eksik_kelime or 0))
        eksik_cumle = max(1, round(eksik / CUMLE_KELIME_ORTALAMA))
        prompt = (
            f"Sen otoXtra'nın Sohbet Yazarısın. {karakter_bilgisi}\n"
            f"{diyalog_kurallari}\n"
            "🚨 GÖREV: SENARYO UZATMA — başka hiçbir işlevin yok.\n"
            f"Aşağıdaki mevcut senaryo {int(mevcut_kelime or 0)} kelime. Hedef TOPLAM {int(hedef_kelime or 0)} kelime ({hedef_kelime_bilgisi}).\n"
            f"Yani yaklaşık {eksik_cumle} YENİ CÜMLE (≈{eksik} kelime) üretmen gerekiyor: cümleleri SAY, kelimeleri tek tek saymaya çalışma.\n"
            "KURALLAR:\n"
            "1) MEVCUT REPLİKLERİ SİLME, KISAALTMA, ÖZETLEME VEYA DÜZELTME: çıktıdaki segments listesi önceki replikleri BİREBİR (aynı kelimeler, aynı sıra) içermek ZORUNDA; birini bile değiştirdiysen çıktı otomatik REDDEDİLİR.\n"
            "2) YENİ replikleri mevcut segmentlerin SONUNA ekle; TTS etiketini YALNIZCA tts_tag alanına yaz ([vurgulu], [alaycı], [savunarak], [şaşırarak]), text alanına veya cümlenin içine ASLA yazma. Doğru speaker ver.\n"
            "3) Yeni replikler SADECE girdideki HOOK / DETECTIVE / FACT LOCK (yalnız OBSERVED-VERIFIED) / EDITORIAL verilerinden beslensin: yeni rakam, karşılaştırma, Türkiye maliyeti, kronik şikayet, gerçek kullanım senaryosu getir. "
            "Aynı fikri farklı cümlelerle yeniden anlatmak MÜKERRETTİR ve çıktı otomatik REDDEDİLİR.\n"
            "4) Kapanış sorusu REPLİK DEĞİLDİR: yorum_tetikleyici_soru alanını AYNEN koru ve segments'e soru cümlesi ekleme — sistem soruyu seslendirmenin EN SONUNA otomatik ekler. "
            "Mevcut senaryonun son repliği bir soruysa bu repliği AYNEN KORU ve yeni replikleri ondan ÖNCE ekle; böylece kapanış yine soruyla biter.\n"
            "5) Çıktın TAM senaryo olsun: önceki replikler + yeni replikler.\n\n"
            f"MEVCUT SENARYO (birebir koru):\n{mevcut_metin}"
            + (f"\n\nYORUM SORUSU (aynen koru): {eski_soru}" if eski_soru else "")
        )
    else:
        prompt = (
            f"Sen otoXtra'nın Sohbet Yazarısın. {karakter_bilgisi}\n"
            "Kanca ve Dedektif verilerini kullanarak doğal bir anlatım yaz.\n"
            f"{diyalog_kurallari}\n"
            "TTS ETİKETLERİ: Duygu/vurgu bilgisini YALNIZCA tts_tag alanına yaz. Örnek: [vurgulu], [alaycı], [savunarak], [şaşırarak], [gülerek]. "
            "Bu etiketler seslendirme talimatıdır; TTS onları kelime olarak OKUMAZ, prosodiye çevirir. "
            "text alanına, cümle başına veya cümlenin içine etiket, parantez içi sahne yönergesi veya 'vurgulu/alaycı/savunarak' kelimesini yönerge diye YAZMA. "
            "text'te yalnız izleyicinin duyması gereken kelimeler olsun.\n"
            "FİNAL: Senaryoyu kesin bir kararla bitirme. Kışkırtıcı SORU'yu YALNIZCA yorum_tetikleyici_soru alanına yaz: "
            "izleyicileri ikiye bölecek, yorumlarda tartışmaya itecek o son soru. Segments listesine soru cümlesi KOYMA — "
            "son replik o soruyu kışkırtan son bilgi/cümledir; sistem soruyu seslendirmenin en sonuna otomatik ekler.\n"
            f"🚨 SÜRE SÖZLEŞMESİ: Video {float(sure_saniye or 0):.0f} saniye. {hedef_kelime_bilgisi or 'Seslendirme video süresine uygun uzunlukta olmalı.'} "
            "Kısa senaryo YASAK: bu hedefin belirgin şekilde altında kalan senaryo otomatik REDDEDİLİR ve yeniden yazdırılır.\n"
            f"{segment_butcesi}\n"
        )
    if feedback:
        prompt += f"\n\n🚨 ELEŞTİRMEN GERİ BİLDİRİMİ (Revize Et): {feedback}"
    if qa_geri_bildirimi:
        prompt += (
            "\n\n🚨 FINAL QA BU SENARYONUN ÖNCEKİ SÜRÜMÜNÜ REDDETTİ. Aşağıdaki sorunların HEPSİNİ düzelt; "
            "Fact Lock'ta OBSERVED/VERIFIED olmayan hiçbir iddia, rakam veya özellik kullanma, emin olmadığın bilgiyi çıkar:\n"
            f"{qa_geri_bildirimi}"
        )
    prompt += vergi_kilidi_talimati(fact_state)

    content = girdi_birlestir(
        durumu_metne_donustur('HOOK', hook_state),
        durumu_metne_donustur('DETECTIVE', detective_state),
        durumu_metne_donustur('FACT LOCK', fact_state),
        durumu_metne_donustur('EDITORIAL', editorial_state),
        durumu_metne_donustur('VIDEO', video_state),
        f"VIDEO SÜRESİ: {sure_saniye} saniye"
    )
    return _run_timed(
        log, "📝 Script Writer Ajan (Senaryo Yazımı)" if mevcut_script is None else "📝 Script Writer Ajan (Senaryo Uzatma)",
        lambda: router.metin_uret(content, prompt, SCRIPT_WRITER_SCHEMA, log, arama_kullan=False),
    )


def _critic_calistir(router, script_state, hook_state, log, qa_geri_bildirimi=""):
    prompt = (
        "Sen trol, şüpheci ve zor beğenen bir Türk izleyicisisin. Bu senaryoyu oku.\n"
        "Robotik mi? Sıkıcı mı? Sonundaki soru yorum yaptıracak kadar kışkırtıcı mı?\n"
        "Eğer 10 üzerinden 7 veya üzeriyse approved: true yap.\n"
        "Değilse approved: false yap ve SADECE NET BİR REVİZE TALİMATI (feedback) ver. Uzatma."
    )
    if qa_geri_bildirimi:
        prompt += (
            "\n\nBAĞLAM: Bu senaryo, Final QA'nın aşağıdaki gerekçelerle reddettiği önceki sürümün "
            "düzeltilmiş hâlidir. Bu sorunlar gerçekten giderilmiş mi, onu da değerlendir; giderilmemişse "
            "approved: false ver. Revize talimatın bu düzeltmeleri GERİ ALDIRMAMALI ve doğrulanmamış "
            "iddia, rakam ya da özellik ekletmemeli:\n"
            f"{qa_geri_bildirimi}"
        )
    content = girdi_birlestir(
        durumu_metne_donustur('HOOK', hook_state),
        durumu_metne_donustur('SCRIPT', script_state)
    )
    return _run_timed(
        log, "🔥 Critic Ajan (Trol İzleyici Eleştirisi)",
        lambda: router.metin_uret(content, prompt, CRITIC_SCHEMA, log, arama_kullan=False),
    )


def _metadata_gen_calistir(router, script_state, hook_state, fact_state, log, ton=None):
    # Caption + hashtag kuralları caption_prompt.txt'teki TEK KAYNAK prompttan
    # gelir (700-850 karakter, TAM 5 hashtag, Fact Lock sınırları, artifact yasağı)
    # + reels_baslik talimatı. Eski 2 satırlık prompt kuralsız çıktı ürettiği
    # için kaldırıldı (Eylül 2026: kısa caption + 7 hashtag).
    prompt = metadata_promptunu_olustur(ton) + vergi_kilidi_talimati(fact_state)
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


def _kapak_basliklarini_hazirla(hook_state, editorial_state, detective_state, log, ajan_basarili=True):
    """Hook ajanının kapak setini kurala göre doğrular; eksikse 5'e tamamlar ve
    hook_state'i güncellenmiş kapak seti ile döndürür (ana == ust)."""
    hook_state = dict(_object_state_or_empty(hook_state))
    ham = hook_state.get("kapak_basliklari") or hook_state.get("kapak_metni") or ""
    if ajan_basarili:
        basliklar = kapak_basliklarini_normalize_et(ham, editorial_state, detective_state, hook_state, log)
    else:
        # Hook ajanı düştü: set yerel fallback'ten geliyor; logda "Hook ajanı 5
        # geçerli alternatif verdi" diye yanıltıcı rapor verilmez.
        basliklar = kapak_basliklarini_normalize_et(ham, editorial_state, detective_state, hook_state)
        log(f"⚠️ Kapak başlıkları: Hook ajanı kullanılamadı; {len(basliklar)} alternatif Editorial/Detective verisinden yerel olarak üretildi.")
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


class _HazirSonuc:
    """Future benzeri: arka plan iş parçacığı açılamazsa senkron sonuç taşır."""

    def __init__(self, value=None, exc=None):
        self._value, self._exc = value, exc

    def result(self, timeout=None):
        if self._exc is not None:
            raise self._exc
        return self._value

    def cancel(self):
        return False


def _arka_plan_baslat(fn, ad):
    """fn'i tek iş parçacıklı havuzda başlatır; havuz açılamazsa senkron çalıştırır."""
    try:
        havuz = ThreadPoolExecutor(max_workers=1, thread_name_prefix=ad)
    except Exception:
        try:
            return _HazirSonuc(fn())
        except Exception as exc:  # pragma: no cover
            return _HazirSonuc(exc=exc)
    future = havuz.submit(fn)
    havuz.shutdown(wait=False)
    return future


def _metadata_arka_planda_baslat(router, script_state, hook_state, fact_state, log, ton=None):
    def _calistir():
        return _istege_bagli_ajan(
            log, "🏷️ Metadata Generator Ajan",
            lambda: _metadata_gen_calistir(router, script_state, hook_state, fact_state, log, ton=ton),
            {},
            router=router,
            atlanabilir=True,
        )

    return _arka_plan_baslat(_calistir, "metadata")


def _metadata_sonucu_al(future, log):
    try:
        state, _ = future.result()
    except Exception as exc:
        log(f"⚠️ 🏷️ Metadata Generator Ajan kullanılamadı; güvenli varsayılanla devam ediliyor: {type(exc).__name__}: {str(exc)[:160]}")
        return {}
    return _object_state_or_empty(state)


# --- Ana döngü -----------------------------------------------------------------

def _mod_coz(mod_karari):
    """mod_karari dict veya (arka planda hesaplanan karar için) dict döndüren
    callable olabilir; geçerli mod adına indirger (varsayılan DUO)."""
    if callable(mod_karari):
        try:
            mod_karari = mod_karari()
        except Exception:
            mod_karari = {}
    mod = str((mod_karari or {}).get("mode") or "DUO").strip().upper() if isinstance(mod_karari, dict) else "DUO"
    return mod if mod in {"DUO", "SOLO_FEMALE", "SOLO_MALE"} else "DUO"


def _hook_uret(router, detective_state, fact_state, editorial_state, log, geri_bildirim=""):
    """Hook ajanını çalıştırır ve kapak setini kurala göre 5 Üst/Alt'a tamamlar."""
    hook_state, hook_model = _istege_bagli_ajan(
        log, "🪝 Hook Generator Ajan",
        lambda: _hook_gen_calistir(router, detective_state, fact_state, editorial_state, log, geri_bildirim=geri_bildirim),
        _hook_fallback(editorial_state),
        router=router,
    )
    ajan_basarili = hook_model not in ("hata", "atlandi")
    if not str(hook_state.get("kapak_metni") or "").strip() and not hook_state.get("kapak_basliklari"):
        hook_state = {**_hook_fallback(editorial_state), **{k: v for k, v in hook_state.items() if v}}
        ajan_basarili = False
    # [Kural: Reels Kapak Yazısı Formatı] — her zaman 5 Üst/Alt alternatifi.
    hook_state, basliklar = _kapak_basliklarini_hazirla(hook_state, editorial_state, detective_state, log, ajan_basarili=ajan_basarili)
    return hook_state, basliklar, ajan_basarili


def _kapak_alanlarini_guncelle(reels_state, hook_state, kapak_basliklari, ilk_uc_saniye=None):
    reels = dict(_object_state_or_empty(reels_state))
    secili = kapak_basliklari[0] if kapak_basliklari else {"ust": hook_state.get("kapak_metni", ""), "alt": ""}
    reels["kapak_basliklari"] = [dict(x) for x in kapak_basliklari]
    reels["hook_families"] = [{
        "kapak_ana": secili.get("ust", ""),
        "kapak_alt": secili.get("alt", ""),
        "ilk_uc_saniye": hook_state.get("ilk_3_saniye_kanca", "") if ilk_uc_saniye is None else ilk_uc_saniye,
    }]
    return reels


def _kapak_kurtarmayi_uygula(future, reels_state, baglam, log):
    """Hook ajanı ilk denemede düştüyse Script/TTS ile paralel yapılan ikinci
    denemenin sonucunu alır; başarılıysa şablon kapakları model kapaklarıyla
    değiştirir. Seslendirmenin açılışı (ilk_uc_saniye) değişmez: o senaryo
    zaten seslendirildi."""
    if future is None:
        return reels_state
    try:
        hook_state, basliklar, basarili = future.result()
    except Exception as exc:
        log(f"⚠️ Kapak kurtarma denemesi hata verdi; yerel kapak seti korunuyor: {type(exc).__name__}: {str(exc)[:160]}")
        return reels_state
    if not basarili or not basliklar:
        log("⚠️ Kapak kurtarma: Hook ajanı ikinci denemede de yanıt vermedi; yerel kapak seti korunuyor.")
        return reels_state
    baglam["hook_state"] = hook_state
    baglam["hook_ajan_basarili"] = True
    ilk = ((_object_state_or_empty(reels_state).get("hook_families") or [{}])[0] or {}).get("ilk_uc_saniye")
    log("✅ Kapak kurtarma: Hook ajanı ikinci denemede yanıt verdi; kapak başlıkları model çıktısıyla güncellendi.")
    return _kapak_alanlarini_guncelle(reels_state, hook_state, basliklar, ilk_uc_saniye=ilk)


def kapaklari_yeniden_uret(router, reels_state, baglam, fact_state, editorial_state, log, qa_geri_bildirimi=""):
    """Yalnız COVER_FAIL için: kapak başlıkları seslendirmeye gömülü değildir;
    Script + TTS yeniden üretilmeden sadece Hook ajanı QA geri bildirimiyle
    tekrar çalıştırılır. reels_state'in kapak alanları güncellenmiş kopyasını döndürür."""
    baglam = baglam if isinstance(baglam, dict) else {}
    detective_state = baglam.get("detective_state") or {}
    hook_state, kapak_basliklari, basarili = _hook_uret(router, detective_state, fact_state, editorial_state, log, geri_bildirim=qa_geri_bildirimi)
    onceki = _object_state_or_empty(reels_state).get("kapak_basliklari")
    if not basarili and onceki and baglam.get("hook_ajan_basarili", True):
        # Yenileme düştü: modelin önceki kapak seti yerel şablonla EZİLMEZ.
        log("⚠️ Kapak yenilemesi: Hook ajanı yanıt vermedi; önceki model kapak seti korunuyor.")
        return reels_state
    baglam["hook_state"] = hook_state
    baglam["hook_ajan_basarili"] = basarili
    # Seslendirme değişmediği için açılış kancası (ilk_uc_saniye) korunur.
    ilk = ((_object_state_or_empty(reels_state).get("hook_families") or [{}])[0] or {}).get("ilk_uc_saniye")
    return _kapak_alanlarini_guncelle(reels_state, hook_state, kapak_basliklari, ilk_uc_saniye=ilk)


def agentic_icerik_uretimi(router, video_state, fact_state, editorial_state, sure_saniye, ton, legacy_voice, log, mod_karari=None, baglam=None, qa_geri_bildirimi=""):
    """4 Ajanlı Viral Üretim Döngüsü (Kelime ve TTS Süre Güvenlik Duvarlı).

    mod_karari: dict veya dict döndüren callable. Callable ise yalnızca Script
      Writer'dan hemen önce çözülür; böylece anlatım modu kararı Detective/Hook
      ile PARALEL hesaplanabilir.
    baglam: (isteğe bağlı, giriş/çıkış) dict. Detective/Hook çıktıları buraya
      yazılır; QA yenilemesinde yeniden kullanılır (aynı veriyle tekrar
      çağrılmaz). baglam["hook_yenile"] True ise Hook yeniden üretilir.
    qa_geri_bildirimi: Final QA'nın reddetme gerekçeleri; Script Writer'a ve
      Critic'e verilir (Critic QA düzeltmelerini denetler, geri aldırmaz).

    Dönüş (9'luplü):
      (reels_state, model, duo_plan, duo_script, ses_basarili,
       kullanilan_ses_modeli, ses_modu, ses_dosyasi, metadata_state)
    """
    baglam = baglam if isinstance(baglam, dict) else {}
    hedef, minimum, maksimum, _, _ = _reels_kelime_ayarlarini_hazirla(sure_saniye, KELIME_HIZI_ORANI)
    hedef_kelime_bilgisi = f"Hedef {hedef} kelime. Kesin aralık {minimum}-{maksimum} kelime."

    # 1. Detective (veri değişmez; QA yenilemesinde bağlamdan yeniden kullanılır,
    # API yoğunsa atlanır)
    # Yalnız BAŞARILI Detective çıktısı yeniden kullanılır; ilk turda düştüyse
    # (boş varsayılan) QA yenilemesinde yeniden denenir.
    if "detective_state" in baglam and baglam.get("detective_basarili", True):
        detective_state = _object_state_or_empty(baglam.get("detective_state"))
        log("♻️ Detective çıktısı önceki turdan yeniden kullanılıyor (API çağrısı yok).")
    else:
        detective_state, detective_model = _istege_bagli_ajan(
            log, "🕵️ Detective Ajan",
            lambda: _detective_calistir(router, video_state, fact_state, editorial_state, log),
            {},
            router=router,
            atlanabilir=True,
        )
        baglam["detective_state"] = detective_state
        baglam["detective_basarili"] = detective_model not in ("hata", "atlandi")

    # 2. Hook Gen — kapak seti burada üretilir. QA yenilemesinde başarılı Hook
    # yeniden kullanılır; ancak QA kanca/kapak/gerçeklik sorunu bildirdiyse
    # (hook_yenile) ya da önceki Hook yerel yedekten geldiyse yeniden üretilir.
    kapak_kurtarma = None
    onceki_hook = baglam.get("hook_state")
    onceki_hook_basarili = baglam.get("hook_ajan_basarili", True)
    if onceki_hook and not baglam.get("hook_yenile") and onceki_hook_basarili:
        hook_state = dict(onceki_hook)
        kapak_basliklari = [dict(x) for x in (hook_state.get("kapak_basliklari") or []) if isinstance(x, dict)]
        log("♻️ Hook/kapak seti önceki turdan yeniden kullanılıyor (API çağrısı yok).")
    else:
        hook_geri_bildirimi = qa_geri_bildirimi if baglam.get("hook_yenile") else ""
        hook_state, kapak_basliklari, hook_basarili = _hook_uret(
            router, detective_state, fact_state, editorial_state, log,
            geri_bildirim=hook_geri_bildirimi,
        )
        if not hook_basarili and onceki_hook and onceki_hook_basarili:
            # Yenileme düştü: modelin önceki kancası yerel şablondan iyidir.
            log("⚠️ Hook yenilemesi yanıt vermedi; önceki model kancası/kapak seti korunuyor.")
            hook_state = dict(onceki_hook)
            kapak_basliklari = [dict(x) for x in (hook_state.get("kapak_basliklari") or []) if isinstance(x, dict)]
            hook_basarili = True
        baglam["hook_state"] = hook_state
        baglam["hook_ajan_basarili"] = hook_basarili
        baglam["hook_yenile"] = False
        if not hook_basarili:
            # Kapak başlıkları seslendirmeye gömülü değil: Hook ajanı Script +
            # Critic + TTS ile PARALEL bir kez daha denenir; yanıt verirse şablon
            # kapaklar model kapaklarıyla değiştirilir (kritik yolda bekleme yok).
            log("🪝 Kapak kurtarma: Hook ajanı Script/TTS ile paralel olarak yeniden deneniyor...")
            kapak_kurtarma = _arka_plan_baslat(
                lambda: _hook_uret(router, detective_state, fact_state, editorial_state, log, geri_bildirim=hook_geri_bildirimi),
                "kapak-kurtarma",
            )

    # Anlatım modu (arka planda hesaplanıyorsa burada sonucu beklenir).
    mod = _mod_coz(mod_karari)

    son_reels = {}
    son_duo_plan = {}
    son_duo_script = {}
    son_model = 'hata'
    turkiye_kancasi = _turkiye_ilgi_kancasi_getir(fact_state)
    segment_butcesi = _segment_butcesi_olustur(hedef, minimum, maksimum, mod)

    # TTS ve Kelime Güvenlik Döngüsü
    #
    # UZATMA STRATEJİSİ (Eylül 2026: 123 sn video, 355 kelime hedef, model
    # 3 kez denemede 155/150/160 kelime üretti → 1.5x hızlandırmaya rağmen
    # videonun sonu ~25 sn sessiz): "sıfırdan daha uzun yaz" talimatı flash
    # serisinde işe yaramıyor. Kısa senaryo artık TAM YAZIM değil, somut UZATMA
    # göreviyle düzeltiliyor: mevcut replikler birebir korunur, eksik miktar
    # CÜMLE SAYISI olarak verilir (flash kelime sayamaz, cümle sayar), yeni
    # replikler kanıt havuzundan (Fact Lock/Detective/Editorial) beslenir.
    # Uzatma geçersizse (açılış kaybolmuş/kısalma, ortadaki replikler yeniden
    # yazılmış = kopuk senaryo, veya mükerrer replik) önceki senaryo korunur
    # ve kalan denemede tekrar denenir.
    #
    # KAPANIŞ SORUSU (2. tur): soru yalnız `yorum_tetikleyici_soru`
    # alanındadır ve sistem onu seslendirmenin EN SONUNA ekler. Model soruyu
    # son replik olarak da yazarsa `_kapanis_sorusunu_ayristir` replik
    # kopyasını atar: uzatmada soru metnin ortasına gömülmez, seslendirme
    # soruyu iki kez okumaz.
    script_state = {}
    strateji = "yaz"  # "yaz" = sıfırdan/yeniden yaz; "uzat" = mevcut senaryoyu eklemeyle uzat
    kritik_atla = False

    for deneme in range(VOICE_REGEN_MAX + 1):
        kritik_atla = False

        # 3. Script Writer (zorunlu ajan: router'ın aşırı-yük tekrarlarına rağmen
        # düşerse hata yukarı taşınır)
        if strateji == "uzat":
            # Kapanış sorusu replik değil: replik listesinde duruyorsa önce
            # çıkar (sistem soruyu en sona ekler) — böylece modelin gördüğü
            # "mevcut senaryo" gövdedir ve soru uzatmada metnin ortasına gömülmez.
            eski_script = _kapanis_sorusunu_ayristir(dict(script_state), log)
            eski_segments = [seg for seg in (eski_script.get("segments") or []) if isinstance(seg, dict) and str(seg.get("text") or "").strip()]
            eski_parcalar = [str(seg.get("text") or "") for seg in eski_segments]
            onceki_metin = _senaryo_metni(eski_script)
            onceki_adet = _kelime_sayisi(onceki_metin)
            script_state, model_script = _script_writer_calistir(
                router, hook_state, detective_state, fact_state, editorial_state,
                video_state, sure_saniye, log, hedef_kelime_bilgisi=hedef_kelime_bilgisi, mod=mod,
                qa_geri_bildirimi=qa_geri_bildirimi, hedef_kelime=hedef,
                mevcut_script=eski_script, mevcut_kelime=onceki_adet, eksik_kelime=hedef - onceki_adet,
            )
            script_state = _object_state_or_empty(script_state)
            # Model soruyu yeniden replik olarak eklediyse de replik kopyası
            # atılır; kapanış tek sefer, en sonda, alan sorusuyla okunur.
            script_state = _kapanis_sorusunu_ayristir(script_state, log)
            yeni_segments = [seg for seg in (script_state.get("segments") or []) if isinstance(seg, dict) and str(seg.get("text") or "").strip()]
            yeni_metin = _senaryo_metni(script_state)
            # Kabul: uzun + açılış korunmuş + TÜM eski replikler aynı sırada
            # birebir (kopuk senaryo yok) + EKLENTİLER mükerrer değil (korunan
            # eski replikler kontrol dışı — birebir kopya sözleşmedir).
            ek_replikler = _ek_replikler_bul(eski_segments, yeni_segments)
            if not _uzatma_gecerli_mi(onceki_metin, yeni_metin, onceki_adet, _kelime_sayisi(yeni_metin), eski_parcalar=eski_parcalar) \
                    or _tekrarli_replik_var_mi(eski_segments, ek_replikler):
                log("⚠️ Uzatma üretimi geçersiz (açılış/koruma ihlali, kopuk senaryo veya mükerrer replik); önceki senaryo korunuyor.")
                script_state = eski_script
                if deneme >= VOICE_REGEN_MAX:
                    log("⚠️ Kelime aralığı hala düzeltilemedi; en iyi mevcut senaryo ile devam ediliyor.")
                    kritik_atla = True
                else:
                    continue
        else:
            script_state, model_script = _script_writer_calistir(
                router, hook_state, detective_state, fact_state, editorial_state,
                video_state, sure_saniye, log, hedef_kelime_bilgisi=hedef_kelime_bilgisi, mod=mod,
                qa_geri_bildirimi=qa_geri_bildirimi, hedef_kelime=hedef, segment_butcesi=segment_butcesi,
            )
            script_state = _object_state_or_empty(script_state)
            # Soru son replik olarak yazıldıysa replik kopyası atılır (bknz.
            # _kapanis_sorusunu_ayristir); seslendirme soruyu iki kez okumaz.
            script_state = _kapanis_sorusunu_ayristir(script_state, log)
        son_model = model_script

        # 4. Critic (isteğe bağlı: düşerse senaryo onaylı sayılır). QA
        # yenilemesinde de çalışır: izleyici gözüyle etkileşim kalitesini Final
        # QA ölçmez; Critic QA gerekçelerini bilerek değerlendirir.
        # (Geçersiz uzatma sonrası son denemede senaryo DEĞİŞMEDİĞİ için
        #  tekrar eleştirtilmez; kelime kontrolüne doğrudan geçilir.)
        if kritik_atla:
            critic_state = {"score": 10, "approved": True, "feedback": ""}
        else:
            critic_state, _ = _istege_bagli_ajan(
                log, "🔥 Critic Ajan",
                lambda: _critic_calistir(router, script_state, hook_state, log, qa_geri_bildirimi=qa_geri_bildirimi),
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
                    video_state, sure_saniye, log, feedback=feedback, hedef_kelime_bilgisi=hedef_kelime_bilgisi, mod=mod,
                    qa_geri_bildirimi=qa_geri_bildirimi,
                )
                revize_state = _object_state_or_empty(revize_state)
                if revize_state.get("segments"):
                    revize_state = _kapanis_sorusunu_ayristir(revize_state, log)
                    script_state, son_model = revize_state, revize_model
                else:
                    log("⚠️ Revize senaryo boş döndü; ilk senaryo korunuyor.")
            except Exception as exc:
                log(f"⚠️ Critic revizesi üretilemedi; ilk senaryo korunuyor: {type(exc).__name__}: {str(exc)[:160]}")

        # Sahne yönergesi konuşulan metinden çıkar; ÖTV kilidi TTS'ten ÖNCE uygulanır
        # ki ses, yanlış tablo satırını okumasın. Etiket tts_tag'de kalır.
        script_state = script_metnini_konusmaya_cek(script_state)
        script_state = senaryoyu_otv_kilidine_cek(script_state, fact_state, log)

        # Reels State Emülasyonu (seslendirme metni tek kaynaktan: segments +
        # kapanış sorusu; replik kopyası yukarıda ayrıştırıldı, soru tam bir
        # kez, en sonda durur.)
        segments = [seg for seg in (script_state.get("segments") or []) if isinstance(seg, dict)]
        soru = str(script_state.get("yorum_tetikleyici_soru") or "").strip()
        full_text = _senaryo_metni(script_state)
        kapak_basliklari = kapaklari_otv_kilidine_cek(kapak_basliklari, fact_state, log)
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
                "ilk_uc_saniye": _metni_kilide_cek(hook_state.get("ilk_3_saniye_kanca", ""), fact_state),
            }],
            "turkiye_ilgi_kancasi": _metni_kilide_cek(turkiye_kancasi, fact_state),
            "metadata": {}
        }

        # TTS segmenti: text = yalnız duyulacak sözler. Vurgu `style` alanındadır;
        # `[vurgulu]` transkripte yazılmaz (2.5 TTS etiketi kelime diye okur).
        tts_segments = []
        for seg in segments:
            text = str(seg.get("text", "") or "").strip()
            if not text:
                continue
            _konusma, stil = replik_tts_hazirla(text, seg.get("tts_tag"))
            text = _konusma or text
            if mod == "SOLO_FEMALE":
                speaker = "female"
            elif mod == "SOLO_MALE":
                speaker = "male"
            else:
                speaker = str(seg.get("speaker") or "female").strip().lower()
                speaker = speaker if speaker in {"female", "male"} else "female"
            tts_segments.append({"speaker": speaker, "text": text, "style": stil, "tts_tag": str(seg.get("tts_tag") or "")})

        if soru:
            last_speaker = tts_segments[-1].get("speaker", "female") if tts_segments else "female"
            final_speaker = "male" if last_speaker == "female" else "female"
            if mod == "SOLO_FEMALE":
                final_speaker = "female"
            elif mod == "SOLO_MALE":
                final_speaker = "male"
            soru_metin, soru_stil = replik_tts_hazirla(soru, "[vurgulu]")
            tts_segments.append({
                "speaker": final_speaker,
                "text": soru_metin or soru,
                "style": soru_stil,
                "tts_tag": "[vurgulu]",
            })

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
                strateji = "yaz"
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
                if adet < minimum:
                    # Kısa senaryo: sıfırdan yeniden yazım aynı kısa metni geri
                    # getiriyordu; yerine mevcut senaryoyu koruyan UZATMA görevi.
                    strateji = "uzat"
                    hedef_kelime_bilgisi = f"Hedef {hedef} kelime. Kesin aralık {minimum}-{maksimum} kelime."
                    log(f'⚠️ Kelime aralığı dışında (sapma %{sapma*100:.0f}, {adet}<{minimum}); mevcut senaryo KORUNARAK yeni repliklerle uzatılıyor ({deneme+1}/{VOICE_REGEN_MAX}).')
                else:
                    strateji = "yaz"
                    hedef_kelime_bilgisi = f"Önceki üretim {adet} kelimeydi — ÇOK UZUN. Hedef {hedef}, izin verilen {minimum}-{maksimum}; metni bu aralığa KISALT (gereksiz tekrar ve dolgu repliklerini çıkar)."
                    log(f'⚠️ Kelime aralığı dışında (sapma %{sapma*100:.0f}, {adet}>{maksimum}); Script Writer yeniden yazıyor ({deneme+1}/{VOICE_REGEN_MAX}).')
                continue
            else:
                log('⚠️ Kelime aralığı hala düzeltilemedi, devam ediliyor.')

        baglam["script_state"] = script_state

        # Metadata yalnız senaryoya bağlıdır (TTS'e değil): TTS (~45 sn) ile
        # EŞZAMANLI başlatılır; seri çalıştırmada ~15 sn ek bekleme yaratıyordu.
        metadata_future = _metadata_arka_planda_baslat(router, script_state, hook_state, fact_state, log, ton=ton)

        # TTS Üretimi
        ses_dosyasi = gecici_ses_yolu()
        ok, info, mod_tts = _duo_ses_veya_legacy_uret(router, duo_script, full_text, legacy_voice, log, ses_dosyasi)

        if not ok:
            # Bu senaryo için başlatılan metadata artık geçersiz; sonucu beklenmez.
            metadata_future.cancel()
            temp_dosya_temizle(ses_dosyasi)
            if deneme < VOICE_REGEN_MAX:
                strateji = "yaz"
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

            # Başarılı TTS ve Süre. Paralel üretilen metadata alınır ve döngüden çıkılır.
            metadata_state = _metadata_sonucu_al(metadata_future, log)
            metadata_state = sosyal_metni_otv_kilidine_cek(
                metadata_state, ("reels_aciklama", "reels_aciklamasi"), fact_state, log, kanal="aciklama",
            )
            reels_state["metadata"] = metadata_state
            reels_state = _kapak_kurtarmayi_uygula(kapak_kurtarma, reels_state, baglam, log)

            return reels_state, "agentic", duo_plan, duo_script, True, info, mod_tts, ses_dosyasi, metadata_state

        metadata_future.cancel()
        temp_dosya_temizle(ses_dosyasi)

    return son_reels, son_model, son_duo_plan, son_duo_script, False, None, "LEGACY", "", {}
