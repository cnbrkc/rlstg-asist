"""Ses ve video işleme fonksiyonları (ffmpeg, hızlandırma, birleştirme)."""
import os, re, wave, shutil, subprocess, tempfile, uuid, json
from config import SES_OMRU_SANIYE, VIDEO_CRF, VIDEO_PRESET, SES_ORNEK_HIZI, SES_KANAL, SES_GENISLIK

_GECICI_SES_DOSYALARI = []
MAKS_VIDEO_HIZLANDIRMA = 1.5
MIN_VIDEO_YAVASLATMA = 0.5
FFMPEG_TIMEOUT = 600
FINAL_AUDIO_SAMPLE_RATE = 48000
FINAL_AUDIO_BITRATE = "192k"

def _ffmpeg_yolu_bul() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"
FFMPEG_BIN = _ffmpeg_yolu_bul()

def gecici_dosya_yolu(onek: str, uzanti: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"{onek}_{uuid.uuid4().hex[:8]}.{uzanti}")
def gecici_ses_yolu() -> str:
    p = gecici_dosya_yolu("ses", "wav")
    _GECICI_SES_DOSYALARI.append(p)
    return p

def wav_yaz(dosya_yolu: str, audio_data: bytes, ornek_hizi: int = SES_ORNEK_HIZI, kanal: int = SES_KANAL, genislik: int = SES_GENISLIK) -> None:
    with wave.open(dosya_yolu, "wb") as wf:
        wf.setnchannels(kanal); wf.setsampwidth(genislik); wf.setframerate(ornek_hizi); wf.writeframes(audio_data)

def temp_dosya_temizle(dosya_yolu: str) -> bool:
    try:
        if dosya_yolu and os.path.exists(dosya_yolu):
            os.remove(dosya_yolu); return True
    except Exception:
        pass
    return False

def eski_ses_dosyalarini_temizle() -> None:
    import time
    now=time.time()
    for p in list(_GECICI_SES_DOSYALARI):
        if not os.path.exists(p) or now-os.path.getmtime(p)>SES_OMRU_SANIYE:
            temp_dosya_temizle(p)
            if p in _GECICI_SES_DOSYALARI: _GECICI_SES_DOSYALARI.remove(p)

def sesi_hizlandir(giris_dosyasi: str, cikti_dosyasi: str, hiz_carpani: float, log_ekle) -> bool:
    if abs(hiz_carpani-1.0)<0.001:
        try: shutil.copy2(giris_dosyasi,cikti_dosyasi); return True
        except Exception as e: log_ekle(f"⚠️ Ses kopyalanamadı: {e}"); return False
    if hiz_carpani<0.5 or hiz_carpani>2.0:
        carpanlar=[]; kalan=hiz_carpani
        while kalan>2.0: carpanlar.append(2.0); kalan/=2.0
        while kalan<0.5: carpanlar.append(0.5); kalan/=0.5
        carpanlar.append(round(kalan,4)); atempo_str=",".join(f"atempo={c}" for c in carpanlar)
    else: atempo_str=f"atempo={hiz_carpani}"
    audio_filter=f"{atempo_str},aresample={FINAL_AUDIO_SAMPLE_RATE}:resampler=soxr:precision=28"
    komut=[FFMPEG_BIN,"-y","-i",giris_dosyasi,"-filter:a",audio_filter,"-ar",str(FINAL_AUDIO_SAMPLE_RATE),"-ac",str(SES_KANAL),"-sample_fmt","s16",cikti_dosyasi]
    try:
        sonuc=subprocess.run(komut,capture_output=True,text=True,timeout=120)
        if sonuc.returncode!=0:
            log_ekle(f"⚠️ ffmpeg hata: {sonuc.stderr[-300:] if sonuc.stderr else 'bilinmeyen'}"); return False
        return True
    except Exception as e: log_ekle(f"⚠️ ffmpeg beklenmeyen hata: {e}"); return False

def _video_bilgi_al(video_yolu: str) -> dict:
    # OpenCV yerine ffprobe kullanılır; böylece her Telegram işinde ağır cv2 bağımlılığı
    # kurulmaz ve medya süresi/FPS tespiti tek bir güvenilir araç üzerinden yapılır.
    bilgi={"fps":0.0,"frames":0,"width":0,"height":0,"duration":0.0}
    try:
        ffprobe=shutil.which("ffprobe") or FFMPEG_BIN.replace("ffmpeg","ffprobe")
        p=subprocess.run([ffprobe,"-v","error","-select_streams","v:0","-show_entries","stream=width,height,avg_frame_rate,nb_frames,duration","-of","json",video_yolu],capture_output=True,text=True,timeout=30)
        data=json.loads(p.stdout or "{}")
        s=(data.get("streams") or [{}])[0]
        bilgi["width"]=int(s.get("width") or 0); bilgi["height"]=int(s.get("height") or 0)
        raw=s.get("avg_frame_rate") or "0/1"
        try:
            n,d=raw.split("/",1); bilgi["fps"]=float(n)/float(d) if float(d) else 0.0
        except Exception: pass
        try: bilgi["frames"]=int(s.get("nb_frames") or 0)
        except Exception: pass
        try: bilgi["duration"]=float(s.get("duration") or 0.0)
        except Exception: pass
    except Exception: pass
    if bilgi["duration"]<=0:
        try:
            r=subprocess.run([FFMPEG_BIN,"-i",video_yolu],capture_output=True,text=True,timeout=30)
            m=re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",r.stderr or "")
            if m: bilgi["duration"]=int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3))
        except Exception: pass
    return bilgi

def _ffprobe_bilgi_al(dosya_yolu: str) -> dict:
    sonuc={"width":0,"height":0,"fps":0.0,"fps_rational":"","duration":0.0,"video_bitrate":0,"audio_sample_rate":0,"audio_channels":0,"audio_bitrate":0,"audio_codec":"","video_codec":""}
    try:
        ffprobe=shutil.which("ffprobe") or FFMPEG_BIN.replace("ffmpeg","ffprobe")
        p=subprocess.run([ffprobe,"-v","error","-show_streams","-show_format","-of","json",dosya_yolu],capture_output=True,text=True,timeout=30)
        data=json.loads(p.stdout or "{}")
        streams=data.get("streams") or []
        video=next((s for s in streams if s.get("codec_type")=="video"),None)
        audio=next((s for s in streams if s.get("codec_type")=="audio"),None)
        if video:
            sonuc["video_codec"]=video.get("codec_name") or ""; sonuc["width"]=int(video.get("width") or 0); sonuc["height"]=int(video.get("height") or 0)
            fps_raw=video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
            try:
                num,den=fps_raw.split("/",1); num_i,den_i=int(num),int(den)
                if den_i: sonuc["fps"]=num_i/den_i; sonuc["fps_rational"]=f"{num_i}/{den_i}"
            except Exception:
                try: sonuc["fps"]=float(fps_raw)
                except Exception: pass
            try: sonuc["duration"]=float(video.get("duration") or 0.0)
            except Exception: pass
            try: sonuc["video_bitrate"]=int(video.get("bit_rate") or 0)
            except Exception: pass
        if audio:
            sonuc["audio_codec"]=audio.get("codec_name") or ""
            try: sonuc["audio_sample_rate"]=int(audio.get("sample_rate") or 0)
            except Exception: pass
            try: sonuc["audio_channels"]=int(audio.get("channels") or 0)
            except Exception: pass
            try: sonuc["audio_bitrate"]=int(audio.get("bit_rate") or 0)
            except Exception: pass
            if sonuc["duration"]<=0:
                try: sonuc["duration"]=float(audio.get("duration") or 0.0)
                except Exception: pass
        if sonuc["duration"]<=0:
            try: sonuc["duration"]=float((data.get("format") or {}).get("duration") or 0.0)
            except Exception: pass
    except Exception:
        eski=_video_bilgi_al(dosya_yolu)
        for key in ("width","height","fps","duration"):
            if eski.get(key): sonuc[key]=eski[key]
    return sonuc

def medya_raporu(dosya_yolu: str, etiket: str, log_ekle) -> dict:
    b=_ffprobe_bilgi_al(dosya_yolu)
    fps=f"{b['fps']:.3f}" if b['fps'] else "?"; sure=f"{b['duration']:.2f}s" if b['duration'] else "?"; coz=f"{b['width']}x{b['height']}" if b['width'] and b['height'] else "?"
    if b["audio_sample_rate"]:
        ses=f"{b['audio_sample_rate']} Hz / {b['audio_channels']}ch / {b['audio_codec'] or '?'}"
        if b["audio_bitrate"]: ses+=f" / {b['audio_bitrate']//1000} kbps"
    else: ses="ses yok"
    log_ekle(f"📐 {etiket}: {coz} | {fps} FPS | {sure} | 🎧 {ses}")
    return b

def video_suresini_al(video_yolu: str) -> float:
    return float(_ffprobe_bilgi_al(video_yolu).get("duration",0.0))

def video_ve_sesi_birlestir(video_yolu: str, ses_yolu: str, cikti_yolu: str, log_ekle) -> bool:
    if not ses_yolu or not os.path.exists(ses_yolu): return False
    input_bilgi=medya_raporu(video_yolu,"INPUT",log_ekle)
    medya_raporu(ses_yolu,"TTS 1.20x SONRASI",log_ekle)
    video_sure=video_suresini_al(video_yolu); ses_sure=_ses_suresini_al(ses_yolu); video_filtresi=None
    if video_sure>0 and ses_sure>0:
        oran=video_sure/ses_sure
        if abs(oran-1.0)>=0.02:
            uygulanan_oran=max(MIN_VIDEO_YAVASLATMA,min(MAKS_VIDEO_HIZLANDIRMA,oran))
            if abs(uygulanan_oran-1.0)>=0.005:
                video_filtresi=f"setpts=PTS/{uygulanan_oran:.6f}"
                log_ekle(f"🎚️ Ses/video süre uyumu: video {video_sure:.2f}s → ses {ses_sure:.2f}s | görüntü hızı {uygulanan_oran:.2f}x")
            if abs(oran-uygulanan_oran)>0.01: log_ekle(f"⚠️ Süre farkı {oran:.2f}x sınırın dışında; güvenli {uygulanan_oran:.2f}x sınırı kullanıldı.")
        else: log_ekle(f"🎚️ Ses/video süre uyumu: fark küçük ({abs(video_sure-ses_sure):.2f}s), hız değişimi yapılmadı.")
    komut=[FFMPEG_BIN,"-y","-i",video_yolu,"-i",ses_yolu]
    if video_filtresi: komut += ["-filter:v",video_filtresi]
    # Kaynak FPS'ini yeniden örneklemiyoruz. FFmpeg mevcut zaman damgalarını H.264'e
    # doğrudan taşır; böylece 30/60 FPS gibi kaynaklar gereksiz yere değişmez ve
    # önceki best_input assertion hatasına yol açan -r/fps_mode kombinasyonu yoktur.
    komut += ["-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset",VIDEO_PRESET,"-crf",str(VIDEO_CRF),"-pix_fmt","yuv420p","-c:a","aac","-ar",str(FINAL_AUDIO_SAMPLE_RATE),"-ac",str(SES_KANAL),"-b:a",FINAL_AUDIO_BITRATE,"-shortest",cikti_yolu]
    try:
        r=subprocess.run(komut,capture_output=True,text=True,timeout=FFMPEG_TIMEOUT)
        if r.returncode!=0: log_ekle(f"⚠️ Video render ffmpeg hatası: {(r.stderr or '')[-800:]}"); return False
        medya_raporu(cikti_yolu,"OUTPUT",log_ekle); return os.path.exists(cikti_yolu) and os.path.getsize(cikti_yolu)>0
    except Exception as e: log_ekle(f"⚠️ Video render hatası: {e}"); return False

def _ses_suresini_al(dosya_yolu: str) -> float:
    try:
        r=subprocess.run([FFMPEG_BIN,"-i",dosya_yolu],capture_output=True,text=True,timeout=30)
        m=re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",r.stderr or "")
        if m: return int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3))
    except Exception: pass
    return 0.0
