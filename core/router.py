import json
import os
import time
from contextlib import contextmanager
import re
import threading
from typing import List, Tuple, Any, Optional

from google import genai
from google.genai import types

from core.config import (
    API_KEYS,
    METIN_MODELLERI,
    ARAMA_MODELLERI,
    SES_MODELLERI,
    VIDEO_ANALIZ_MODELLERI,
    COOLDOWN_BULUNAMADI,
    COOLDOWN_DIGER,
    COOLDOWN_FREE_TIER_YOK,
    ISTEK_ZAMAN_ASIMI_MS,
    ISTEGE_BAGLI_AJAN_BUTCESI_SANIYE,
    ASIRI_YUK_ATLAMA_PENCERESI_SANIYE,
    model_arama_destekliyor_mu,
)
from core.media import sesi_hizlandir, temp_dosya_temizle, wav_yaz, gecici_dosya_yolu

# İstemci düzeyi varsayılan zaman aşımı (ms). Her istek ayrıca kendi profiline
# göre daha kısa bir zaman aşımı taşır (bkz. ISTEK_PROFILLERI).
REQUEST_TIMEOUT_MS = max(ISTEK_ZAMAN_ASIMI_MS.values())

# İstek profilleri: {zaman_asimi_ms, butce_saniye}. Eylül 2026 logunda 60 sn'lik
# tek zaman aşımı, yoğunlukta asılı kalan her denemeye ~1 dk kaybettiriyordu
# (bir Critic çağrısı 236 sn, Detective 77 sn, anlatım modu 67 sn boşa gitti).
#   metin        : Script Writer / Caption / Threads / Metadata (kısa-orta JSON)
#   uzun_metin   : Editorial / Fact Lock / Final QA (uzun JSON)
#   istege_bagli : güvenli varsayılanı olan ajanlar; toplam süre bütçesi de var
#   video        : Forensic video analizi (inline video upload)
#   tts          : tek/çoklu ses üretimi
ISTEK_PROFILLERI = {
    "metin": {"zaman_asimi_ms": ISTEK_ZAMAN_ASIMI_MS["metin"], "butce_saniye": None},
    "uzun_metin": {"zaman_asimi_ms": ISTEK_ZAMAN_ASIMI_MS["uzun_metin"], "butce_saniye": None},
    "istege_bagli": {"zaman_asimi_ms": ISTEK_ZAMAN_ASIMI_MS["istege_bagli"], "butce_saniye": ISTEGE_BAGLI_AJAN_BUTCESI_SANIYE},
    "video": {"zaman_asimi_ms": ISTEK_ZAMAN_ASIMI_MS["video"], "butce_saniye": None},
    "tts": {"zaman_asimi_ms": ISTEK_ZAMAN_ASIMI_MS["tts"], "butce_saniye": None},
}

# Bir deneme bu süreden uzun sürüp hata verirse (timeout/yavaş 503) model "yavaş"
# sayılır: SİLİNMEZ, yalnızca 3 dk boyunca listenin sonuna atılır.
SLOW_ATTEMPT_SECONDS = 20
SLOW_MODEL_COOLDOWN = 180
SLOW_HITS_BEFORE_NEXT_MODEL = 2
# Günlük kota (PerDay) o gün geri gelmez; bu çalışma boyunca atlanır.
DAILY_QUOTA_COOLDOWN = 6 * 60 * 60

# Aşırı yük koruması: Bir isteğin TÜM model+key kombinasyonları yalnızca geçici
# hatalarla (503 / dakikalık kota / zaman aşımı) düşerse, istek hemen exception
# fırlatıp tüm pipeline'ı öldürmez. Kısa bir bekleme sonrası tam tur yeniden
# yapılır. Tek tek denemeler arasında bekleme YOK (hızlı tam tur felsefesi
# korunur); bekleme yalnızca komple başarısız bir turdan sonra devreye girer.
def guvenli_json_yukle(metin: str) -> dict:
    """Model yanıtındaki JSON'u güvenli biçimde ayrıştırır (kod bloğu/serbest metin toleranslı)."""
    if not metin:
        raise ValueError("Model boş yanıt verdi.")
    metin = metin.strip()
    if metin.startswith("```"):
        metin = re.sub(r"^```(?:json)?\s*|\s*```$", "", metin, flags=re.I | re.S).strip()
    try:
        return json.loads(metin)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", metin, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


OVERLOAD_RETRY_ROUNDS = _env_int("ROUTER_OVERLOAD_RETRY_ROUNDS", 2)
# Eski 20/40/60 sn beklemeler tek bir Editorial isteğini 4+ dakikaya taşıyordu;
# 503 dalgaları genelde saniyeler içinde açılıyor, kısa bekleme + hızlı tam tur yeter.
OVERLOAD_RETRY_WAITS = (15, 30, 45)
# Tüm çalışma boyunca aşırı-yük beklemelerine ayrılan toplam süre (saniye).
# GitHub Actions job'u 30 dk ile sınırlı; bu bütçe bitince eski davranışa
# (hemen hata) dönülür ve job zaman aşımına düşmez.
OVERLOAD_WAIT_BUDGET_SECONDS = _env_int("ROUTER_OVERLOAD_WAIT_BUDGET", 180)
# Router oluşturulduktan bu kadar saniye sonra yeni aşırı-yük beklemesi
# başlatılmaz (job 30 dk; render + Telegram upload için pay bırakılır).
OVERLOAD_RETRY_DEADLINE_SECONDS = _env_int("ROUTER_OVERLOAD_RETRY_DEADLINE", 12 * 60)
_sleep = time.sleep

# ---
# Yönlendirme felsefesi (kullanıcı talebi):
# Geçici (503 / kota / zaman aşımı) hatalarda BEKLEME veya dakikalarca yasaklama
# YOK. Her model tüm API key'lerinde 1'er kez, seri biçimde denenir; bir tam tur
# (tüm key'ler) başarısız olursa bir sonraki modele geçilir. Aynı modelın 3.
# key'inde hata verip 4. key'inde çalışabileceği ihtimali her zaman korunur.
# Geçici yasağın adımlar (pipeline adımları) arası hafızası yoktur: her adım
# turları fresh olarak yeniden yapar.
#
# İSTİSNA (Eylül 2026 üretim durması sonrası): Bir isteğin TAMAMI (tüm modeller
# x tüm key'ler) geçici hatayla düşerse istek hemen exception fırlatmaz; 20/40/60
# sn bekleyip tam turu en fazla OVERLOAD_RETRY_ROUNDS kez tekrarlar. Toplam
# bekleme OVERLOAD_WAIT_BUDGET_SECONDS ve OVERLOAD_RETRY_DEADLINE_SECONDS ile
# sınırlıdır; tek tek key denemeleri arasında hâlâ bekleme yoktur.
#
# Yalnızca iki durum "kalıcı" sayılır ve adımlar boyunca hatırlanır ( zaman
# tasarrufu için; key'den bağımsız her key'de aynı sonucu verirler):
#   * 404 / model_config  -> model düzeyinde yasak (*+model)
#   * free_tier_yok       -> key/project'e bağlı, yalnızca o key yasaklanır
# ---


class SmartRouter:
    def __init__(self) -> None:
        # blacklist: yalnızca KALICI yasaklar. Geçici hatalar buraya yazılmaz.
        #   "*+{model}"        -> model düzeyi (404 / bozuk config)
        #   "{mail}+{model}"   -> key düzeyi (free-tier)
        self.blacklist = {}
        self._slow_models = {}
        self.clients = {}
        self.request_counter = 0
        self._overload_wait_spent = 0.0
        self._created_at = time.monotonic()
        # >0 iken aşırı-yük beklemesi yapılmaz (güvenli varsayılanı olan
        # isteğe bağlı adımlar bütçeyi zorunlu adımlara bırakır).
        self._overload_retry_off = 0
        # Son "tam tur aşırı yük" (tüm model+key geçici hata) anı; atlanabilir
        # ajanlar bu pencere içinde hiç denenmeden güvenli varsayılana düşer.
        self._last_full_overload_at = None
        self._aktif_profil = None
        self._request_counter_lock = threading.Lock()
        for mail, api_key in self._ordered_api_items():
            if api_key and api_key.strip():
                self.clients[mail] = genai.Client(
                    api_key=api_key.strip(),
                    http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
                )

    def _ordered_api_items(self):
        def _rank(name: str):
            if name == "GEMINI_API_KEY":
                return (0, 0)
            m = re.match(r"^GEMINI_API_KEY_(\d+)$", str(name))
            if m:
                return (1, int(m.group(1)))
            return (2, str(name))

        return sorted(API_KEYS.items(), key=lambda kv: _rank(kv[0]))

    def _ban(self, mail: str, model: str, cooldown: int, scope: str) -> None:
        key = f"*+{model}" if scope == "model" else (f"{mail}+*" if scope == "key" else f"{mail}+{model}")
        self.blacklist[key] = time.time() + cooldown

    def _is_model_banned(self, model: str) -> bool:
        """Model düzeyinde kalıcı yasağı kontrol eder (404 / bozuk config)."""
        key = f"*+{model}"
        bl = self.blacklist
        if key in bl:
            if time.time() < bl[key]:
                return True
            bl.pop(key, None)
        return False

    def _mark_slow(self, model: str) -> None:
        self._slow_models[model] = time.time() + SLOW_MODEL_COOLDOWN

    def _is_slow(self, model: str) -> bool:
        until = self._slow_models.get(model)
        if until is None:
            return False
        if time.time() < until:
            return True
        self._slow_models.pop(model, None)
        return False

    def _is_key_banned(self, mail: str, model: str) -> bool:
        """Yalnızca bu key'e özel yasağı kontrol eder (ör. free-tier)."""
        now = time.time()
        bl = self.blacklist
        for key in (f"{mail}+*", f"{mail}+{model}"):
            if key in bl:
                if now < bl[key]:
                    return True
                bl.pop(key, None)
        return False

    def _parse_hata(self, hata_metni: str) -> Tuple[str, int]:
        m = (hata_metni or "").lower()
        if "404" in m or "not_found" in m or "model not found" in m:
            return "model_key", COOLDOWN_BULUNAMADI
        if "limit: 0" in m or 'limit\\": 0' in m:
            return "free_tier_yok", COOLDOWN_FREE_TIER_YOK
        if "429" in m or "resource_exhausted" in m or "quota" in m or "rate limit" in m:
            if "perday" in m.replace(" ", "").replace("_", ""):
                return "quota_day", DAILY_QUOTA_COOLDOWN
            return "quota", 0
        if "400" in m or "invalid_argument" in m or "unsupported" in m:
            return "model_config", COOLDOWN_BULUNAMADI
        if "503" in m or "unavailable" in m:
            return "unavailable", COOLDOWN_DIGER
        if "timeout" in m or "timed out" in m:
            return "combo", COOLDOWN_DIGER
        return "combo", COOLDOWN_DIGER

    @staticmethod
    def _reason_for(scope: str) -> str:
        return {
            "unavailable": "503 servis yoğunluğu",
            "combo": "zaman aşımı/geçici hata",
        }.get(scope, "geçici hata")

    @staticmethod
    def _config_with_timeout(config, timeout_ms):
        """İstek config'ine profil zaman aşımını ekler (istemci varsayılanını ezer)."""
        if config is None or not timeout_ms:
            return config
        try:
            if getattr(config, "http_options", None) is not None:
                return config
            return config.model_copy(update={"http_options": types.HttpOptions(timeout=int(timeout_ms))})
        except Exception:
            return config

    def _make_request(
        self,
        model_listesi: List[str],
        contents: Any,
        config,
        log_ekle,
        stop_on_quota=False,
        require_text=False,
        profil: Optional[str] = None,
    ):
        son_hata = None
        modeller = list(model_listesi or [])
        # Açık profil > pipeline'ın `istek_profili()` bağlamı > varsayılan "metin".
        profil = profil or getattr(self, "_aktif_profil", None) or "metin"
        profil_ayari = ISTEK_PROFILLERI.get(profil) or ISTEK_PROFILLERI["metin"]
        config = self._config_with_timeout(config, profil_ayari.get("zaman_asimi_ms"))
        # İsteğe bağlı ajanlarda toplam süre bütçesi: bütçe dolunca kalan
        # model+key kombinasyonları denenmez, çağıran güvenli varsayılana düşer.
        butce_saniye = profil_ayari.get("butce_saniye")
        butce_doldu = False
        # Yakın zamanda yavaş/timeout veren modeller silinmez, yalnızca sona atılır.
        modeller = [m for m in modeller if not self._is_slow(m)] + [m for m in modeller if self._is_slow(m)]
        # Search araçlı isteklerde gelen kota hatası aracın kotası olabilir; günlük yasağı bunlara uygulama.
        has_tools = bool(getattr(config, "tools", None))
        request_started = time.perf_counter()
        # Caption ve Threads bağımsız kolları eşzamanlı çalışabilir. Log request
        # kimlikleri yarışıp aynı numarayı almasın diye yalnız bu küçük sayaç
        # bölgesini kilitliyoruz; ağ çağrıları paralel kalmaya devam eder.
        with self._request_counter_lock:
            self.request_counter = getattr(self, "request_counter", 0) + 1
            request_id = self.request_counter
        log_ekle(
            f"🌐 API REQUEST #{request_id} START | models={len(modeller)} | "
            f"keys={len(self._ordered_api_items())} | content={type(contents).__name__} | "
            f"profil={profil} timeout={int(profil_ayari.get('zaman_asimi_ms') or 0)//1000}s"
            + (f" bütçe={butce_saniye}s" if butce_saniye else "")
        )

        max_rounds = 1 if getattr(self, "_overload_retry_off", 0) else OVERLOAD_RETRY_ROUNDS + 1
        for round_no in range(1, max_rounds + 1):
            # Bu turda en az bir GEÇİCİ hata (503 / dakikalık kota / timeout /
            # boş yanıt) görüldüyse bekleyip tekrar denemeye değer. Tüm hatalar
            # kalıcıysa (404 / config / free-tier / günlük kota) beklemek boşuna.
            round_transient = 0
            if round_no > 1:
                modeller = [m for m in model_listesi or [] if not self._is_slow(m)] + [m for m in model_listesi or [] if self._is_slow(m)]
            for model_adi in modeller:
                model_started = time.perf_counter()
                # Kalıcı model yasağı (404 / bozuk config) → tüm key'lerde geçersiz,
                # bu modeli tamamen atla. (Adımlar boyunca korunur.)
                if self._is_model_banned(model_adi):
                    continue
                log_ekle(f"🧠 Model deneniyor: {model_adi}")
                slow_hits = 0

                for mail, _api_key in self._ordered_api_items():
                    if butce_saniye and (time.perf_counter() - request_started) >= butce_saniye:
                        log_ekle(f"⏱️ API #{request_id}: isteğe bağlı ajan bütçesi ({butce_saniye}s) doldu; kalan denemeler atlanıyor.")
                        butce_doldu = True
                        break
                    # Bu key'e özel kalıcı yasak (free-tier) → bu key'i atla, diğer
                    # key denenir (farklı key/project farklı tier'a sahip olabilir).
                    if self._is_key_banned(mail, model_adi):
                        continue
                    client = self.clients.get(mail)
                    if client is None:
                        continue

                    attempt_started = time.perf_counter()
                    log_ekle(f"↳ API #{request_id} deneme START | {mail}+{model_adi}")
                    try:
                        response = client.models.generate_content(
                            model=model_adi,
                            contents=contents,
                            config=config,
                        )
                    except Exception as e:
                        son_hata = e
                        attempt_elapsed = time.perf_counter() - attempt_started
                        scope, cooldown = self._parse_hata(str(e))

                        if scope in ("quota", "quota_day"):
                            # Kota key'e özeldir ve dolar; beklemeden diğer key.
                            if stop_on_quota:
                                raise
                            if scope == "quota_day" and not has_tools:
                                self._ban(mail, model_adi, cooldown, "combo")
                                log_ekle(f"🚫 {mail}+{model_adi}: günlük kota bitti; bu çalışma boyunca atlanacak. | {attempt_elapsed:.2f}s")
                                continue
                            round_transient += 1
                            log_ekle(f"⚠️ {mail}+{model_adi}: kota dolu; sonraki key deneniyor. | {attempt_elapsed:.2f}s")
                            continue
                        if scope == "free_tier_yok":
                            # Tier key/project'e bağlı → yalnızca bu key yasakla.
                            self._ban(mail, model_adi, cooldown, "combo")
                            log_ekle(f"🚫 {mail}+{model_adi}: free tier'da yok; sonraki key deneniyor. | {attempt_elapsed:.2f}s")
                            continue
                        if scope in ("model_key", "model_config"):
                            # Model yok / config uyumsuz → tüm key'lerde geçersiz.
                            self._ban(mail, model_adi, cooldown, "model")
                            log_ekle(f"⚠️ {model_adi}: bu model/config desteklenmiyor; sonraki modele geçiliyor. | {attempt_elapsed:.2f}s")
                            break  # bu modelin kalan key'lerini atla → sonraki model

                        # unavailable (503) / combo (timeout) / bilinmeyen -> GEÇICI.
                        # Bekleme yok, yasaklama yok: hemen diğer key. Bir tam tur
                        # (tüm key'ler) tamamlanmadan diğer modele geçilmez; bir
                        # sonraki pipeline adımında bu model yeniden fresh denenir.
                        round_transient += 1
                        log_ekle(f"⚠️ {mail}+{model_adi}: {self._reason_for(scope)}; sonraki key deneniyor. | {attempt_elapsed:.2f}s")
                        if attempt_elapsed >= SLOW_ATTEMPT_SECONDS:
                            self._mark_slow(model_adi)
                            slow_hits += 1
                            if slow_hits >= SLOW_HITS_BEFORE_NEXT_MODEL:
                                log_ekle(f"⏱️ {model_adi}: {slow_hits} yavaş deneme; kalan key'ler atlanıp sıradaki modele geçiliyor.")
                                break
                        continue

                    attempt_elapsed = time.perf_counter() - attempt_started
                    if require_text and not str(getattr(response, "text", "") or "").strip():
                        log_ekle(f"⚠️ {mail}+{model_adi}: yanıt boş; sonraki key/model deneniyor. | {attempt_elapsed:.2f}s")
                        son_hata = ValueError("Model boş yanıt verdi.")
                        round_transient += 1
                        continue

                    total_elapsed = time.perf_counter() - request_started
                    model_elapsed = time.perf_counter() - model_started
                    log_ekle(
                        f"✅ Başarılı → {mail} + {model_adi} | deneme {attempt_elapsed:.2f}s | "
                        f"model turu {model_elapsed:.2f}s | API toplam {total_elapsed:.2f}s ({total_elapsed/60:.2f} dk)"
                    )
                    return response, f"{mail}+{model_adi}"

                if butce_doldu:
                    break

            if round_transient == 0:
                break
            if round_transient:
                self._last_full_overload_at = time.monotonic()
            if butce_doldu or round_no >= max_rounds:
                break
            wait_s = OVERLOAD_RETRY_WAITS[min(round_no - 1, len(OVERLOAD_RETRY_WAITS) - 1)]
            spent = getattr(self, "_overload_wait_spent", 0.0)
            remaining = OVERLOAD_WAIT_BUDGET_SECONDS - spent
            age = time.monotonic() - getattr(self, "_created_at", time.monotonic())
            if age + wait_s > OVERLOAD_RETRY_DEADLINE_SECONDS:
                log_ekle(f"⏳ API #{request_id}: çalışma süresi {age/60:.1f} dk; job zaman sınırı için yeniden deneme beklemesi yapılmıyor.")
                break
            if remaining <= 0:
                log_ekle(f"⏳ API #{request_id}: aşırı-yük bekleme bütçesi ({OVERLOAD_WAIT_BUDGET_SECONDS}s) tükendi; yeniden deneme yapılmıyor.")
                break
            wait_s = min(wait_s, remaining)
            self._overload_wait_spent = spent + wait_s
            log_ekle(
                f"⏳ API #{request_id}: tüm model+key kombinasyonları geçici hata verdi (503/kota/timeout). "
                f"{wait_s:.0f}s beklenip tam tur yeniden deneniyor ({round_no}/{OVERLOAD_RETRY_ROUNDS})."
            )
            _sleep(wait_s)

        total_elapsed = time.perf_counter() - request_started
        log_ekle(f"🌐 API REQUEST #{request_id} FAIL | toplam {total_elapsed:.2f}s ({total_elapsed/60:.2f} dk)")
        raise son_hata if son_hata else Exception("Tüm model+key kombinasyonları başarısız.")

    def yakin_zamanda_asiri_yuk_var_mi(self, pencere_saniye=None) -> bool:
        """Son `pencere_saniye` içinde bir istek TAM TUR (tüm model+key) geçici
        hatayla düştüyse True. Atlanabilir ajanlar bu durumda API'yi hiç yormadan
        güvenli varsayılanla devam eder; bekleme bütçesi zorunlu adımlara kalır."""
        son = getattr(self, "_last_full_overload_at", None)
        if son is None:
            return False
        pencere = ASIRI_YUK_ATLAMA_PENCERESI_SANIYE if pencere_saniye is None else pencere_saniye
        return (time.monotonic() - son) < pencere

    @contextmanager
    def istek_profili(self, profil: str):
        """Bu blok içindeki isteklere zaman aşımı / süre bütçesi profili uygular
        (bkz. ISTEK_PROFILLERI). Pipeline ajanları `metin_uret` imzasını
        değiştirmeden bu bağlamla kısa zaman aşımı alır."""
        onceki = getattr(self, "_aktif_profil", None)
        self._aktif_profil = profil if profil in ISTEK_PROFILLERI else onceki
        try:
            yield self
        finally:
            self._aktif_profil = onceki

    @contextmanager
    def hizli_basarisizlik(self):
        """Bu blok içindeki isteklerde aşırı-yük beklemesi yapılmaz (tek tam tur)."""
        self._overload_retry_off = getattr(self, "_overload_retry_off", 0) + 1
        try:
            yield self
        finally:
            self._overload_retry_off = max(0, getattr(self, "_overload_retry_off", 1) - 1)

    def _json_parse_or_none(self, text: str):
        try:
            return guvenli_json_yukle(text)
        except Exception:
            return None

    def metin_uret(
        self,
        icerik: Any,
        system_prompt: str,
        response_schema: dict,
        log_ekle,
        model_listesi=None,
        arama_kullan=True,
        profil: Optional[str] = None,
    ):
        model_listesi = model_listesi or (ARAMA_MODELLERI if arama_kullan else METIN_MODELLERI)
        if profil is not None and profil not in ISTEK_PROFILLERI:
            profil = None

        if arama_kullan:
            kwargs = dict(system_instruction=system_prompt)
            if model_listesi and model_arama_destekliyor_mu(model_listesi[0]):
                kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        else:
            kwargs = dict(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=response_schema,
            )

        try:
            response, info = self._make_request(
                model_listesi,
                icerik,
                types.GenerateContentConfig(**kwargs),
                log_ekle,
                stop_on_quota=False,
                require_text=True,
                profil=profil,
            )
            parsed = self._json_parse_or_none(getattr(response, "text", ""))
            if parsed is not None:
                return parsed, info

            if arama_kullan:
                log_ekle("⚠️ Research JSON parse edilemedi; Search'siz structured-output fallback deneniyor.")
                fallback_models = [m for m in METIN_MODELLERI if m not in model_listesi]
                if not fallback_models:
                    fallback_models = list(METIN_MODELLERI)
                fallback_kwargs = dict(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                )
                response, info = self._make_request(
                    fallback_models,
                    icerik,
                    types.GenerateContentConfig(**fallback_kwargs),
                    log_ekle,
                    stop_on_quota=False,
                    require_text=True,
                    profil=profil,
                )
                return guvenli_json_yukle(getattr(response, "text", "")), info

            raise ValueError("Model JSON yanıtı parse edilemedi.")

        except Exception as first_error:
            if not arama_kullan:
                raise

            log_ekle(
                f"⚠️ Research/Search rotası başarısız; Search'siz Fact Lock fallback deneniyor: {str(first_error)[:180]}"
            )
            fallback_models = list(METIN_MODELLERI)
            fallback_kwargs = dict(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=response_schema,
            )
            try:
                response, info = self._make_request(
                    fallback_models,
                    icerik,
                    types.GenerateContentConfig(**fallback_kwargs),
                    log_ekle,
                    stop_on_quota=False,
                    require_text=True,
                    profil=profil,
                )
                return guvenli_json_yukle(getattr(response, "text", "")), info
            except Exception:
                raise first_error

    def video_analiz_et(
        self,
        video_bytes: bytes,
        mime_type: str,
        system_prompt: str,
        response_schema: dict,
        log_ekle,
        model_listesi=None,
        arama_kullan=False,
    ):
        model_listesi = model_listesi or VIDEO_ANALIZ_MODELLERI
        part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        kwargs = dict(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=response_schema,
        )
        if arama_kullan and model_listesi and model_arama_destekliyor_mu(model_listesi[0]):
            kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]

        response, info = self._make_request(
            model_listesi,
            [part],
            types.GenerateContentConfig(**kwargs),
            log_ekle,
            require_text=True,
            profil="video",
        )
        return guvenli_json_yukle(getattr(response, "text", "")), info

    def _tts_performans_promptu_olustur(self, metin: str, ses_adi: str) -> str:
        return (
            f"Perform this Turkish automotive voiceover with the configured voice {ses_adi}. "
            "Natural human delivery, not a newsreader and not robotic. Use conversational intonation, "
            "sentence-level emphasis, subtle dynamic energy, and short natural breaths/pauses at punctuation. "
            "Let excitement, curiosity, surprise, seriousness or amusement follow the meaning of each line. "
            "Do not flatten the whole performance into one emotional register. Keep the transcript exactly as provided; "
            "add no words, omit no words, and do not read formatting instructions aloud.\n\n"
            f"TRANSCRIPT:\n{metin}"
        )

    def _tts_coklu_promptu_olustur(self, metin: str, speaker_names) -> str:
        names = ", ".join(speaker_names)
        return (
            "# AUDIO PROFILE\n"
            f"A real Turkish couple/partner conversation between {names} while watching an automotive clip together. "
            "They know each other well: attentive, quick, relaxed and credible. Neither is a host, announcer, actor or singer.\n\n"
            "# SCENE\n"
            "The listener should feel they overheard one spontaneous, tightly edited exchange recorded in the same room. "
            "There is no audience, stage, studio presentation, podcast intro, music rhythm or turn-taking ceremony.\n\n"
            "# DIRECTOR'S NOTES\n"
            "Style: Underplay it. Speak in contemporary conversational Turkish with grounded confidence and dry warmth. "
            "React to the meaning of the previous line before advancing your own point. Do not sound polished in the same way. "
            "Do not sing, chant, harmonize, trade symmetrical phrases, use a bouncing cadence, or perform this like a duet.\n"
            "Turn-taking: Handoffs on rebuttals and short reactions should be immediate, as if the next speaker was already listening. "
            "Use a pause only where thought or punctuation genuinely needs one. Never insert the same pause between every speaker. "
            "Consecutive lines by one speaker should feel like that person briefly holding the floor, not restarting a performance.\n"
            "Pacing: Fast, alert cold open in the first two turns; then natural variation. Short reactions stay short and dry; "
            "evidence lines may breathe slightly; the reversal lands with a tiny thought beat; the callback ends with momentum, not an outro.\n"
            "Dynamics: Keep emotional changes subtle and content-led. No alternating 'excited/serious/amazed' pattern, exaggerated gasps, "
            "radio smile, theatrical projection or overacting. Distinct personalities, shared acoustic space.\n"
            "Integrity: Keep every spoken word exactly as provided, in order. Add no fillers, laughter or sound effects; omit no words. "
            "Do not read speaker labels or these directions aloud.\n\n"
            f"# TRANSCRIPT BETWEEN {names}\n{metin}"
        )

    def _tts_response_audio_bytes(self, response):
        try:
            for cand in getattr(response, "candidates", []) or []:
                for part in getattr(getattr(cand, "content", None), "parts", []) or []:
                    data = getattr(getattr(part, "inline_data", None), "data", None)
                    if data:
                        return data
        except Exception:
            pass
        raise ValueError("TTS yanıtında audio bulunamadı")

    def _tts_kaydet(self, audio: bytes, cikti_dosyasi: str, hiz_carpani: float, log_ekle) -> Tuple[bool, Optional[str]]:
        if abs(hiz_carpani - 1.0) < 0.001:
            wav_yaz(cikti_dosyasi, audio)
            return True

        raw = gecici_dosya_yolu("ses_ham", "wav")
        wav_yaz(raw, audio)
        ok = sesi_hizlandir(raw, cikti_dosyasi, hiz_carpani, log_ekle)
        temp_dosya_temizle(raw)
        return ok

    def ses_uret(
        self,
        metin: str,
        ses_adi: str,
        cikti_dosyasi: str,
        log_ekle,
        hiz_carpani: float = 1.0,
    ) -> Tuple[bool, Optional[str]]:
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=ses_adi)
                )
            ),
        )
        try:
            with self.istek_profili("tts"):
                response, info = self._make_request(
                    SES_MODELLERI,
                    self._tts_performans_promptu_olustur(metin, ses_adi),
                    config,
                    log_ekle,
                )
            audio = self._tts_response_audio_bytes(response)
            ok = self._tts_kaydet(audio, cikti_dosyasi, hiz_carpani, log_ekle)
            return (ok, info if ok else None)
        except Exception as e:
            log_ekle(f"❌ TTS başarısız: {str(e)[:200]}")
            return False, None

    def coklu_ses_uret(
        self,
        metin: str,
        speaker_voices: List[Tuple[str, str]],
        cikti_dosyasi: str,
        log_ekle,
        hiz_carpani: float = 1.0,
    ) -> Tuple[bool, Optional[str]]:
        # Gemini multi-speaker TTS tam olarak iki speaker config ister. Tek
        # speaker veya yinelenen etiket kabul edilirse istek başarılı görünse
        # bile model tek sese düşebilir; bu yüzden burada fail-closed davran.
        if not metin or len(speaker_voices or []) != 2:
            log_ekle("❌ Çoklu TTS config reddedildi: tam olarak iki konuşmacı gerekli.")
            return False, None
        try:
            configs = []
            names = []
            voices = []
            for speaker, voice in speaker_voices:
                speaker = str(speaker).strip()
                voice = str(voice).strip()
                if not speaker or not voice:
                    return False, None
                names.append(speaker)
                voices.append(voice)
                configs.append(
                    types.SpeakerVoiceConfig(
                        speaker=speaker,
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                        ),
                    )
                )

            if len(set(names)) != 2 or len(set(voices)) != 2:
                log_ekle("❌ Çoklu TTS config reddedildi: speaker etiketleri ve sesler farklı olmalı.")
                return False, None

            # ÖNEMLİ: GenerateContentConfig.speech_config alanı SpeechConfig
            # bekler. MultiSpeakerVoiceConfig doğrudan bu alana verilirse
            # google-genai/Pydantic onu sessizce boş bir SpeechConfig'e çevirir:
            # API'ye `speechConfig: {}` gider ve model tek bir ses seçer. Önceki
            # "başarılı ama tek sesli" üretimin kök nedeni buydu.
            multi_speaker_config = types.MultiSpeakerVoiceConfig(
                speaker_voice_configs=configs
            )
            speech_config = types.SpeechConfig(
                multi_speaker_voice_config=multi_speaker_config
            )
            config = types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=speech_config,
            )
            prompt = self._tts_coklu_promptu_olustur(metin, names)
            log_ekle(
                "🔐 Multi-speaker API config doğrulandı: "
                + " + ".join(f"{speaker}={voice}" for speaker, voice in zip(names, voices))
            )

            with self.istek_profili("tts"):
                response, info = self._make_request(
                    SES_MODELLERI,
                    prompt,
                    config,
                    log_ekle,
                )
            audio = self._tts_response_audio_bytes(response)
            ok = self._tts_kaydet(audio, cikti_dosyasi, hiz_carpani, log_ekle)
            return (ok, info if ok else None)
        except Exception as e:
            log_ekle(f"❌ Çoklu TTS başarısız: {str(e)[:220]}")
            return False, None
