import time, re, os
from typing import List, Tuple, Any, Optional
from google import genai
from google.genai import types
from config import API_KEYS, METIN_MODELLERI, ARAMA_MODELLERI, SES_MODELLERI, VIDEO_ANALIZ_MODELLERI, COOLDOWN_SUNUCU, COOLDOWN_BULUNAMADI, COOLDOWN_DIGER, COOLDOWN_FREE_TIER_YOK, IP_BAN_KORUMA, model_arama_destekliyor_mu
from utils import guvenli_json_yukle
from media import sesi_hizlandir, temp_dosya_temizle, wav_yaz, gecici_dosya_yolu

class SmartRouter:
    def __init__(self) -> None:
        self.blacklist = {}
        self._last_request_had_quota = False
        self.clients = {}
        for mail, api_key in API_KEYS.items():
            if api_key and api_key.strip():
                self.clients[mail] = genai.Client(api_key=api_key.strip())

    def _is_banned(self, mail: str, model: str) -> bool:
        now=time.time(); bl=self.blacklist
        for key in [f"*+{model}",f"{mail}+*",f"{mail}+{model}"]:
            if key in bl:
                if now<bl[key]: return True
                del bl[key]
        return False
    def _ban(self, mail: str, model: str, cooldown: int, scope: str) -> None:
        key=f"*+{model}" if scope=="model" else (f"{mail}+*" if scope=="key" else f"{mail}+{model}")
        self.blacklist[key]=time.time()+cooldown
    def _clear_cooldowns(self, model_listesi=None) -> None:
        if not model_listesi: self.blacklist.clear(); return
        modeller=set(model_listesi)
        for key in list(self.blacklist):
            if "+" in key and key.split("+",1)[1] in modeller: self.blacklist.pop(key,None)
    def _retry_delay_cikar(self,hata_metni:str)->int:
        for pattern in [r'retryDelay["\':\s]+(\d+)',r'retry in (\d+(?:\.\d+)?)s']:
            m=re.search(pattern,hata_metni,re.I)
            if m:
                try:return int(float(m.group(1)))+1
                except: pass
        return 0
    def _parse_hata(self,hata_metni:str)->Tuple[str,int]:
        m=(hata_metni or "").lower()
        if "404" in m or "not_found" in m or "model not found" in m:
            # Gemini may report model availability as key/user-specific. Do not
            # blacklist the model globally in that case; let the next API key try it.
            if "no longer available to new users" in m or "new users" in m:
                return "model_key",COOLDOWN_BULUNAMADI
            return "model",COOLDOWN_BULUNAMADI
        if "limit: 0" in m or 'limit": 0' in m: return "free_tier_yok",COOLDOWN_FREE_TIER_YOK
        if "429" in m or "resource_exhausted" in m or "quota" in m or "rate limit" in m: return "quota",0
        if "400" in m or "invalid_argument" in m or "unsupported" in m: return "model_config",COOLDOWN_BULUNAMADI
        if "503" in m or "unavailable" in m: return "combo",COOLDOWN_SUNUCU
        return "combo",COOLDOWN_DIGER
    def _handle_hata(self,mail,model,hata_metni,log_ekle)->str:
        scope,cooldown=self._parse_hata(hata_metni)
        if scope=="free_tier_yok": self._ban(mail,model,cooldown,"model"); log_ekle(f"🚫 {model} free tier'da yok."); return "break_model"
        if scope=="quota":
            self._last_request_had_quota=True; delay=self._retry_delay_cikar(hata_metni)
            if delay>0: time.sleep(min(delay,60)); return "retry"
            self._ban(mail,model,COOLDOWN_SUNUCU,"combo"); return "quota"
        if scope=="model_key":
            self._ban(mail,model,cooldown,"combo")
            log_ekle(f"⚠️ {mail}+{model}: bu key/model erişimi yok; sonraki key deneniyor.")
            return "continue"
        self._ban(mail,model,cooldown,"model" if scope in ("model","model_config") else "combo")
        log_ekle(f"⚠️ {mail}+{model}: {scope}")
        return "break_model" if scope in ("model","model_config") else "continue"

    def _make_request(self,model_listesi:List[str],contents:Any,config,log_ekle,stop_on_quota=False,son_fallback=True):
        son_hata=None; self._last_request_had_quota=False
        modeller=list(model_listesi or [])
        if son_fallback:
            for fallback in ["gemini-3.1-flash-lite","gemini-2.5-flash","gemini-2.5-flash-lite"]:
                if fallback not in modeller: modeller.append(fallback)
        # If an entire model family is returning 503, do not spend another full
        # retry cycle on every key before reaching the next family. A single
        # transient retry per key is enough; the next model remains the fallback.
        for model_adi in modeller:
            log_ekle(f"🧠 Model deneniyor: {model_adi}")
            for mail,api_key in API_KEYS.items():
                if self._is_banned(mail,model_adi): continue
                client = self.clients.get(mail)
                if client is None: continue
                _503_deneme=0
                while True:
                    try:
                        response=client.models.generate_content(model=model_adi,contents=contents,config=config)
                        log_ekle(f"✅ Başarılı → {mail} + {model_adi}"); return response,f"{mail}+{model_adi}"
                    except Exception as e:
                        son_hata=e; hata_metni=str(e)
                        if ("503" in hata_metni or "unavailable" in hata_metni.lower()) and _503_deneme < 1:
                            _503_deneme += 1
                            log_ekle(f"⏳ {mail}+{model_adi}: 503 geçici hata, 5s sonra tekrar deneniyor (1/1)")
                            time.sleep(5)
                            continue
                        aksiyon=self._handle_hata(mail,model_adi,hata_metni,log_ekle)
                        if stop_on_quota and aksiyon=="quota": raise
                        if aksiyon in ("break_model","quota"): break
                        break
        raise son_hata if son_hata else Exception("Tüm model+key kombinasyonları başarısız.")

    def metin_uret(self,icerik:Any,system_prompt:str,response_schema:dict,log_ekle,model_listesi=None,arama_kullan=True):
        model_listesi=model_listesi or (ARAMA_MODELLERI if arama_kullan else METIN_MODELLERI)
        kwargs=dict(system_instruction=system_prompt,response_mime_type="application/json",response_schema=response_schema)
        if arama_kullan and model_listesi and model_arama_destekliyor_mu(model_listesi[0]): kwargs["tools"]=[types.Tool(google_search=types.GoogleSearch())]
        try:
            response,info=self._make_request(model_listesi,icerik,types.GenerateContentConfig(**kwargs),log_ekle,stop_on_quota=arama_kullan)
        except Exception:
            if arama_kullan and self._last_request_had_quota:
                self._clear_cooldowns(model_listesi); kwargs.pop("tools",None)
                response,info=self._make_request(model_listesi,icerik,types.GenerateContentConfig(**kwargs),log_ekle)
            else: raise
        return guvenli_json_yukle(getattr(response,"text","")),info

    def video_analiz_et(self,video_bytes:bytes,mime_type:str,system_prompt:str,response_schema:dict,log_ekle,model_listesi=None,arama_kullan=False):
        model_listesi=model_listesi or VIDEO_ANALIZ_MODELLERI
        part=types.Part.from_bytes(data=video_bytes,mime_type=mime_type)
        kwargs=dict(system_instruction=system_prompt,response_mime_type="application/json",response_schema=response_schema)
        if arama_kullan and model_listesi and model_arama_destekliyor_mu(model_listesi[0]): kwargs["tools"]=[types.Tool(google_search=types.GoogleSearch())]
        response,info=self._make_request(model_listesi,[part],types.GenerateContentConfig(**kwargs),log_ekle)
        return guvenli_json_yukle(getattr(response,"text","")),info

    def _tts_performans_promptu_olustur(self,metin:str,ses_adi:str)->str:
        return f"Speak naturally in Turkish as a professional automotive presenter. Voice: {ses_adi}. Keep the transcript exactly as provided. Add no extra words.\n\nTRANSCRIPT:\n{metin}"
    def _tts_response_audio_bytes(self,response):
        try:
            for cand in getattr(response,"candidates",[]) or []:
                for part in getattr(getattr(cand,"content",None),"parts",[]) or []:
                    data=getattr(getattr(part,"inline_data",None),"data",None)
                    if data: return data
        except Exception: pass
        raise ValueError("TTS yanıtında audio bulunamadı")
    def ses_uret(self,metin:str,ses_adi:str,cikti_dosyasi:str,log_ekle,hiz_carpani:float=1.0)->Tuple[bool,Optional[str]]:
        config=types.GenerateContentConfig(response_modalities=["AUDIO"],speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=ses_adi))))
        try:
            response,info=self._make_request(SES_MODELLERI,self._tts_performans_promptu_olustur(metin,ses_adi),config,log_ekle,son_fallback=False)
            audio=self._tts_response_audio_bytes(response)
            if abs(hiz_carpani-1.0)<.001: wav_yaz(cikti_dosyasi,audio); return True,info
            raw=gecici_dosya_yolu("ses_ham","wav"); wav_yaz(raw,audio)
            ok=sesi_hizlandir(raw,cikti_dosyasi,hiz_carpani,log_ekle); temp_dosya_temizle(raw)
            return (ok,info if ok else None)
        except Exception as e:
            log_ekle(f"❌ TTS başarısız: {str(e)[:200]}"); return False,None
