import time
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
    model_arama_destekliyor_mu,
)
from core.utils import guvenli_json_yukle
from core.media import sesi_hizlandir, temp_dosya_temizle, wav_yaz, gecici_dosya_yolu

REQUEST_TIMEOUT_MS = 60_000

# ---
# Yönlendirme felsefesi (kullanıcı talebi):
# Geçici (503 / kota / zaman aşımı) hatalarda BEKLEME veya dakikalarca yasaklama
# YOK. Her model tüm API key'lerinde 1'er kez, seri biçimde denenir; bir tam tur
# (tüm key'ler) başarısız olursa bir sonraki modele geçilir. Aynı modelın 3.
# key'inde hata verip 4. key'inde çalışabileceği ihtimali her zaman korunur.
# Geçici yasağın adımlar (pipeline adımları) arası hafızası yoktur: her adım
# turları fresh olarak yeniden yapar.
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
        self.clients = {}
        self.request_counter = 0
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

    def _make_request(
        self,
        model_listesi: List[str],
        contents: Any,
        config,
        log_ekle,
        stop_on_quota=False,
        require_text=False,
    ):
        son_hata = None
        modeller = list(model_listesi or [])
        request_started = time.perf_counter()
        # Caption ve Threads bağımsız kolları eşzamanlı çalışabilir. Log request
        # kimlikleri yarışıp aynı numarayı almasın diye yalnız bu küçük sayaç
        # bölgesini kilitliyoruz; ağ çağrıları paralel kalmaya devam eder.
        with self._request_counter_lock:
            self.request_counter = getattr(self, "request_counter", 0) + 1
            request_id = self.request_counter
        log_ekle(
            f"🌐 API REQUEST #{request_id} START | models={len(modeller)} | "
            f"keys={len(self._ordered_api_items())} | content={type(contents).__name__}"
        )

        for model_adi in modeller:
            model_started = time.perf_counter()
            # Kalıcı model yasağı (404 / bozuk config) → tüm key'lerde geçersiz,
            # bu modeli tamamen atla. (Adımlar boyunca korunur.)
            if self._is_model_banned(model_adi):
                continue
            log_ekle(f"🧠 Model deneniyor: {model_adi}")

            for mail, _api_key in self._ordered_api_items():
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

                    if scope == "quota":
                        # Kota key'e özeldir ve dolar; beklemeden diğer key.
                        if stop_on_quota:
                            raise
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
                    log_ekle(f"⚠️ {mail}+{model_adi}: {self._reason_for(scope)}; sonraki key deneniyor. | {attempt_elapsed:.2f}s")
                    continue

                attempt_elapsed = time.perf_counter() - attempt_started
                if require_text and not str(getattr(response, "text", "") or "").strip():
                    log_ekle(f"⚠️ {mail}+{model_adi}: yanıt boş; sonraki key/model deneniyor. | {attempt_elapsed:.2f}s")
                    son_hata = ValueError("Model boş yanıt verdi.")
                    continue

                total_elapsed = time.perf_counter() - request_started
                model_elapsed = time.perf_counter() - model_started
                log_ekle(
                    f"✅ Başarılı → {mail} + {model_adi} | deneme {attempt_elapsed:.2f}s | "
                    f"model turu {model_elapsed:.2f}s | API toplam {total_elapsed:.2f}s ({total_elapsed/60:.2f} dk)"
                )
                return response, f"{mail}+{model_adi}"

        total_elapsed = time.perf_counter() - request_started
        log_ekle(f"🌐 API REQUEST #{request_id} FAIL | toplam {total_elapsed:.2f}s ({total_elapsed/60:.2f} dk)")
        raise son_hata if son_hata else Exception("Tüm model+key kombinasyonları başarısız.")

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
    ):
        model_listesi = model_listesi or (ARAMA_MODELLERI if arama_kullan else METIN_MODELLERI)

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
            f"Perform the following Turkish automotive dialogue as a natural two-person conversation between {names}. "
            "Use the configured voice for each named speaker and keep each speaker's identity stable. "
            "This is an expressive conversational performance: vary intonation and energy with the meaning, use subtle "
            "curiosity, confidence, surprise, amusement or seriousness when appropriate, and leave short natural pauses "
            "between turns and at punctuation. Avoid a robotic, flat, studio-announcer delivery. Do not overact. "
            "Keep every spoken word exactly as provided; add no words, omit no words, and do not read speaker labels aloud. "
            "Preserve order and conversational timing.\n\n"
            f"TRANSCRIPT:\n{metin}"
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
