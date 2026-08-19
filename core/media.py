"""Ses ve video işleme fonksiyonları (ffmpeg, hızlandırma, birleştirme)."""
import os, re, wave, shutil, subprocess, tempfile, uuid, json
from core.config import VIDEO_CRF, VIDEO_PRESET, SES_ORNEK_HIZI, SES_KANAL, SES_GENISLIK

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
    return gecici_dosya_yolu("ses", "wav")

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

def sesi_hizlandir(giris_dosyasi: str, cikti_dosyasi: str, hiz_carpani: float, log_ekle) -> bool:
    if abs(hiz_carpani-1.0)<0.001:
        try: shutil.copy2(giris_dosyasi,cikti_dosyasi); return True
        except Exception as e: log_ekle(f"⚠️ Ses kopyalanamadı: {e}"); return False
    if hiz_carpani<0.5 or hiz_carpani>2.0:
        carpanlar=[]; kalan=hiz_carpani
        while kalan>2.0: carpanlar.append(2.0); kalan/=2.0
        while kalan<0.5: carpanlar.append(0.5); kalan/=0.5
        carpanlar.append(round(kalan,4)); atempo_filtreleri=carpanlar
    else:
        atempo_filtreleri=[hiz_carpani]

    # Bazı FFmpeg 7.x build'lerinde WAV -> WAV işlemini aynı filter graph
    # içinde atempo + aresample ile yapmak "Assertion best_input >= 0"
    # hatasına yol açabiliyor. Filtreleme ve resampling'i iki ayrı, basit
    # FFmpeg çağrısına ayırıyoruz. Böylece TTS hızlandırma mantığı değişmeden
    # WAV çıktısı güvenilir biçimde üretiliyor.
    filtreler=[f"atempo={c}" for c in atempo_filtreleri]
    atempo_filter=",".join(filtreler)
    ara_dosya=gecici_dosya_yolu("atempo", "wav")
    try:
        komut1=[FFMPEG_BIN,"-y","-i",giris_dosyasi,"-af",atempo_filter,"-c:a","pcm_s16le",ara_dosya]
        sonuc=subprocess.run(komut1,capture_output=True,text=True,timeout=120)
        if sonuc.returncode!=0 or not os.path.exists(ara_dosya) or os.path.getsize(ara_dosya)<=44:
            log_ekle(f"⚠️ ffmpeg atempo hata: {sonuc.stderr[-500:] if sonuc.stderr else 'bilinmeyen'}")
            return False

        komut2=[FFMPEG_BIN,"-y","-i",ara_dosya,"-ar",str(FINAL_AUDIO_SAMPLE_RATE),"-ac",str(SES_KANAL),"-sample_fmt","s16","-c:a","pcm_s16le",cikti_dosyasi]
        sonuc=subprocess.run(komut2,capture_output=True,text=True,timeout=120)
        if sonuc.returncode!=0:
            log_ekle(f"⚠️ ffmpeg resample hata: {sonuc.stderr[-500:] if sonuc.stderr else 'bilinmeyen'}")
            return False
        if not os.path.exists(cikti_dosyasi) or os.path.getsize(cikti_dosyasi)<=44:
            log_ekle("⚠️ ffmpeg çıktı dosyası boş/geçersiz."); return False
        return True
    except Exception as e:
        log_ekle(f"⚠️ ffmpeg beklenmeyen hata: {e}"); return False
    finally:
        temp_dosya_temizle(ara_dosya)

def _ffprobe_yolu() -> str:
    """ffprobe binary yolunu döndürür; yoksa boş string.

    imageio_ffmpeg yalnızca ffmpeg'i paketler, ffprobe'yi değil. Eskiden
    FFMPEG_BIN.replace("ffmpeg","ffprobe") var olmayan bir yola işaret edip
    sessizce FileNotFoundError fırlatıyordu; bu yüzden tüm çözünürlük/FPS/kodek
    bilgisi kayboluyordu. Artık yol gerçekten var mı diye kontrol ediyoruz.
    """
    candidate = shutil.which("ffprobe")
    if candidate:
        return candidate
    candidate = FFMPEG_BIN.replace("ffmpeg", "ffprobe")
    if candidate and candidate != FFMPEG_BIN and os.path.exists(candidate):
        return candidate
    return ""


def _parse_ffmpeg_stderr(stderr: str) -> dict:
    """``ffmpeg -i`` stderr metninden akış bilgisini ayrıştırır (ffprobe yedeği).

    ffprobe kullanılamadığında tek güvenilir kaynak ffmpeg'in açılış banner
    çıktısıdır. Aşağıdaki gibi satırları işler:
      Duration: 00:01:31.83, start: 0.000000, bitrate: 4908 kb/s
      Stream #0:0(...): Video: h264 (...), yuv420p, 1080x1920 [...], 4908 kb/s, 30 fps, 30 tbr, 90k tbn
      Stream #0:1(...): Audio: aac (LC), 48000 Hz, stereo, fltp, 127 kb/s
    """
    out = {"duration": 0.0, "width": 0, "height": 0, "fps": 0.0, "video_codec": "",
           "video_bitrate": 0, "audio_sample_rate": 0, "audio_channels": 0,
           "audio_bitrate": 0, "audio_codec": ""}
    stderr = stderr or ""

    dm = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if dm:
        try:
            out["duration"] = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3))
        except Exception:
            pass

    vline = next((l for l in stderr.splitlines() if " Video:" in l), "")
    if vline:
        cm = re.search(r"Video:\s*([A-Za-z0-9_]+)", vline)
        if cm:
            out["video_codec"] = cm.group(1)
        # "Video: h264 ..., 1080x1920 ..." — satırdaki ilk WxH.
        dm = re.search(r"(\d{2,5})x(\d{2,5})", vline)
        if dm:
            out["width"], out["height"] = int(dm.group(1)), int(dm.group(2))
        fm = re.search(r"(\d+(?:\.\d+)?)\s*fps", vline)
        if not fm:
            fm = re.search(r"(\d+(?:\.\d+)?)\s*tbr", vline)
        if fm:
            try:
                out["fps"] = float(fm.group(1))
            except Exception:
                pass
        bm = re.search(r"(\d+)\s*kb/s", vline)
        if bm:
            try:
                out["video_bitrate"] = int(bm.group(1)) * 1000
            except Exception:
                pass

    aline = next((l for l in stderr.splitlines() if " Audio:" in l), "")
    if aline:
        cm = re.search(r"Audio:\s*([A-Za-z0-9_]+)", aline)
        if cm:
            out["audio_codec"] = cm.group(1)
        hz = re.search(r"(\d+)\s*Hz", aline)
        if hz:
            try:
                out["audio_sample_rate"] = int(hz.group(1))
            except Exception:
                pass
        chm = re.search(r"Hz,\s*(mono|stereo|\d+(?:\.\d+)?)", aline)
        if chm:
            chmap = {"mono": 1, "stereo": 2, "2.1": 3, "3.0": 3, "4.0": 4,
                     "5.0": 5, "5.1": 6, "6.1": 7, "7.1": 8}
            out["audio_channels"] = chmap.get(chm.group(1), 0)
        bm = re.search(r"(\d+)\s*kb/s", aline)
        if bm:
            try:
                out["audio_bitrate"] = int(bm.group(1)) * 1000
            except Exception:
                pass
    return out


def _ffprobe_bilgi_al(dosya_yolu: str) -> dict:
    sonuc={"width":0,"height":0,"fps":0.0,"fps_rational":"","duration":0.0,"video_bitrate":0,"audio_sample_rate":0,"audio_channels":0,"audio_bitrate":0,"audio_codec":"","video_codec":""}
    ffprobe = _ffprobe_yolu()
    if ffprobe:
        try:
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
            pass

    # ffprobe yoksa veya çözünürlük/FPS/kodek/süre bilgisi eksik kaldıysa,
    # ffmpeg -i stderr'ini tek seferde ayrıştırıp eksik alanları tamamla. Bu,
    # imageio_ffmpeg (yalnızca ffmpeg) kullanan ortamlarda medya_raporu'nun
    # "? | ? FPS | ses yok" gibi yanlış/eksik bilgi göstermesini engeller.
    if not sonuc["width"] or not sonuc["height"] or not sonuc["fps"] \
            or sonuc["duration"] <= 0 or not sonuc["audio_sample_rate"]:
        try:
            r = subprocess.run([FFMPEG_BIN, "-hide_banner", "-i", dosya_yolu],
                               capture_output=True, text=True, timeout=30)
            parsed = _parse_ffmpeg_stderr(r.stderr or "")
            for k, v in parsed.items():
                if not sonuc.get(k) and v:
                    sonuc[k] = v
        except Exception:
            pass
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

def _kalite_filtresi_olustur(input_bilgi: dict, log_ekle) -> tuple[str | None, int | None]:
    """Kaynak çözünürlüğünü küçültmeden, hafif bir perceptual kalite geçişi hazırlar.

    AI super-resolution (Real-ESRGAN) araştırıldı; ancak GitHub-hosted runner'da
    GPU garantisi olmadığı için her videoda CPU tabanlı frame-by-frame inference
    çalıştırmak pipeline süresini gereksiz yere uzatabilir. Bunun yerine final
    render'da hafif Lanczos ölçekleme + kontrollü unsharp kullanıyoruz.
    Bu gerçek detay üretmez; kaynak zaten düşükse sadece algılanan netliği artırır.
    """
    width=int(input_bilgi.get("width") or 0); height=int(input_bilgi.get("height") or 0)
    if width<=0 or height<=0:
        return None, None
    # Telegram sıkıştırması nedeniyle beklenenden düşük bir kaynak gelirse
    # 576x1024'e kontrollü Lanczos upscale yapılır; gerçek detay geri getirilemez.
    target_w, target_h = 576, 1024
    if width < target_w and height < target_h:
        filtre=f"scale={target_w}:{target_h}:flags=lanczos,unsharp=5:5:0.30:5:5:0.0"
        log_ekle(f"✨ Hafif kalite geçişi: {width}x{height} → {target_w}x{target_h} (Lanczos + kontrollü netlik)")
        return filtre, target_w
    # Normal kaynaklarda çözünürlüğü değiştirmeden sadece hafif netlik.
    return "unsharp=5:5:0.30:5:5:0.0", width

def video_ve_sesi_birlestir(video_yolu: str, ses_yolu: str, cikti_yolu: str, log_ekle) -> bool:
    """TTS'yi 1.20x sonrası gerçek süresiyle videoya tam senkronlar.

    TTS hızı değişmez: SES_HIZ_CARPANI = 1.20x. Video, TTS'nin gerçek süresine
    (yukarı yuvarlanmış saniye) göre hızlandırılır veya yavaşlatılır.
    """
    if not ses_yolu or not os.path.exists(ses_yolu):
        return False
    input_bilgi = medya_raporu(video_yolu, "INPUT", log_ekle)
    medya_raporu(ses_yolu, "TTS 1.20x SONRASI", log_ekle)
    video_sure = video_suresini_al(video_yolu)
    ses_sure = _ses_suresini_al(ses_yolu)
    if video_sure <= 0 or ses_sure <= 0:
        log_ekle("❌ Senkron için geçerli video/TTS süresi alınamadı.")
        return False
    import math
    hedef_sure = float(max(1, math.ceil(ses_sure)))
    video_hiz = video_sure / hedef_sure
    if video_hiz > MAKS_VIDEO_HIZLANDIRMA:
        video_hiz = MAKS_VIDEO_HIZLANDIRMA
        hedef_sure = video_sure / video_hiz
        log_ekle(f"⚠️ TTS çok kısa; video hızlandırma sınırı {MAKS_VIDEO_HIZLANDIRMA:.2f}x uygulandı.")
    elif video_hiz < MIN_VIDEO_YAVASLATMA:
        video_hiz = MIN_VIDEO_YAVASLATMA
        hedef_sure = video_sure / video_hiz
        log_ekle(f"⚠️ TTS çok uzun; video yavaşlatma sınırı {MIN_VIDEO_YAVASLATMA:.2f}x uygulandı.")
    speed_filter = f"setpts=PTS/{video_hiz:.8f}"
    hareket = 'hızlandırma' if video_hiz > 1.001 else 'yavaşlatma' if video_hiz < 0.999 else '1:1'
    log_ekle(f"🎚️ VIDEO-TTS SENKRONU: video {video_sure:.2f}s + TTS {ses_sure:.2f}s → hedef {hedef_sure:.2f}s | video hızı {video_hiz:.3f}x ({hareket}).")
    kalite_filtresi, _ = _kalite_filtresi_olustur(input_bilgi, log_ekle)
    video_filtresi = speed_filter
    if kalite_filtresi:
        video_filtresi = f"{video_filtresi},{kalite_filtresi}"
    output_fps = input_bilgi.get("fps") or 30.0
    komut = [FFMPEG_BIN, "-y", "-i", video_yolu, "-i", ses_yolu, "-filter:v", video_filtresi, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", str(VIDEO_CRF), "-pix_fmt", "yuv420p", "-r", f"{output_fps:.6f}", "-af", "apad", "-c:a", "aac", "-ar", str(FINAL_AUDIO_SAMPLE_RATE), "-ac", str(SES_KANAL), "-b:a", FINAL_AUDIO_BITRATE, "-t", f"{hedef_sure:.6f}", cikti_yolu]
    try:
        r = subprocess.run(komut, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT)
        if r.returncode != 0:
            log_ekle(f"⚠️ Video render ffmpeg hatası: {(r.stderr or '')[-800:]}")
            return False
        medya_raporu(cikti_yolu, "OUTPUT", log_ekle)
        return os.path.exists(cikti_yolu) and os.path.getsize(cikti_yolu) > 0
    except Exception as e:
        log_ekle(f"⚠️ Video render hatası: {e}")
        return False

def _ses_suresini_al(dosya_yolu: str) -> float:
    try:
        r=subprocess.run([FFMPEG_BIN,"-i",dosya_yolu],capture_output=True,text=True,timeout=30)
        m=re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",r.stderr or "")
        if m: return int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3))
    except Exception: pass
    return 0.0
