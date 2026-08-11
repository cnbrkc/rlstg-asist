"""Ses ve video işleme fonksiyonları (ffmpeg, hızlandırma, birleştirme)."""
import os, re, wave, math, shutil, subprocess, tempfile, uuid
from config import SES_OMRU_SANIYE, VIDEO_CRF, VIDEO_PRESET, SES_ORNEK_HIZI, SES_KANAL, SES_GENISLIK

_GECICI_SES_DOSYALARI = []
MAKS_VIDEO_HIZLANDIRMA = 1.5
MIN_VIDEO_YAVASLATMA = 0.5
FFMPEG_TIMEOUT = 600

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
    komut=[FFMPEG_BIN,"-y","-i",giris_dosyasi,"-filter:a",atempo_str,"-ar",str(SES_ORNEK_HIZI),"-ac",str(SES_KANAL),"-sample_fmt","s16",cikti_dosyasi]
    try:
        sonuc=subprocess.run(komut,capture_output=True,text=True,timeout=120)
        if sonuc.returncode!=0:
            log_ekle(f"⚠️ ffmpeg hata: {sonuc.stderr[-300:] if sonuc.stderr else 'bilinmeyen'}"); return False
        return True
    except Exception as e: log_ekle(f"⚠️ ffmpeg beklenmeyen hata: {e}"); return False

def _video_bilgi_al(video_yolu: str) -> dict:
    bilgi={"fps":0.0,"frames":0,"width":1920,"height":1080,"duration":0.0}
    try:
        import cv2
        cap=cv2.VideoCapture(video_yolu)
        if cap.isOpened():
            bilgi["fps"]=float(cap.get(cv2.CAP_PROP_FPS)); bilgi["frames"]=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); bilgi["width"]=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); bilgi["height"]=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if bilgi["fps"]>0: bilgi["duration"]=bilgi["frames"]/bilgi["fps"]
            cap.release()
    except Exception: pass
    if bilgi["duration"]<=0:
        try:
            r=subprocess.run([FFMPEG_BIN,"-i",video_yolu],capture_output=True,text=True,timeout=30)
            m=re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",r.stderr or "")
            if m: bilgi["duration"]=int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3))
        except Exception: pass
    return bilgi

def video_suresini_al(video_yolu: str) -> float:
    return float(_video_bilgi_al(video_yolu).get("duration",0.0))

def video_ve_sesi_birlestir(video_yolu: str, ses_yolu: str, cikti_yolu: str, log_ekle) -> bool:
    if not ses_yolu or not os.path.exists(ses_yolu): return False

    # TTS tarafında SES_HIZ_CARPANI (şu an 1.2x) uygulanıyor.
    # Ardından burada video ile son TTS süresi karşılaştırılır. Arada anlamlı
    # fark varsa görüntü, sesi kesip biçmek yerine sese tam oturacak şekilde
    # kontrollü olarak hızlandırılır/yavaşlatılır. 0.5x–1.5x sınırları
    # korunur; küçük farklarda videoya gereksiz hız filtresi uygulanmaz.
    video_sure = video_suresini_al(video_yolu)
    ses_sure = _ses_suresini_al(ses_yolu)
    video_filtresi = None
    if video_sure > 0 and ses_sure > 0:
        oran = video_sure / ses_sure
        if abs(oran - 1.0) >= 0.02:
            uygulanan_oran = max(MIN_VIDEO_YAVASLATMA, min(MAKS_VIDEO_HIZLANDIRMA, oran))
            if abs(uygulanan_oran - 1.0) >= 0.005:
                video_filtresi = f"setpts=PTS/{uygulanan_oran:.6f}"
                log_ekle(f"🎚️ Ses/video süre uyumu: video {video_sure:.2f}s → ses {ses_sure:.2f}s | görüntü hızı {uygulanan_oran:.2f}x")
            if abs(oran - uygulanan_oran) > 0.01:
                log_ekle(f"⚠️ Süre farkı {oran:.2f}x sınırın dışında; güvenli {uygulanan_oran:.2f}x sınırı kullanıldı.")
        else:
            log_ekle(f"🎚️ Ses/video süre uyumu: fark küçük ({abs(video_sure-ses_sure):.2f}s), hız değişimi yapılmadı.")

    komut=[FFMPEG_BIN,"-y","-i",video_yolu,"-i",ses_yolu]
    if video_filtresi:
        komut += ["-filter:v", video_filtresi]
    komut += ["-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset",VIDEO_PRESET,"-crf",str(VIDEO_CRF),"-c:a","aac","-shortest",cikti_yolu]
    try:
        r=subprocess.run(komut,capture_output=True,text=True,timeout=FFMPEG_TIMEOUT)
        if r.returncode!=0:
            log_ekle(f"⚠️ Video render ffmpeg hatası: {(r.stderr or '')[-500:]}"); return False
        return os.path.exists(cikti_yolu)
    except Exception as e: log_ekle(f"⚠️ Video render hatası: {e}"); return False

def _ses_suresini_al(dosya_yolu: str) -> float:
    try:
        r=subprocess.run([FFMPEG_BIN,"-i",dosya_yolu],capture_output=True,text=True,timeout=30)
        m=re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",r.stderr or "")
        if m: return int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3))
    except Exception: pass
    return 0.0
