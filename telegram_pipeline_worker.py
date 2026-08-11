import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path

import requests

from config import TON_DENGELI, TON_EGLENCE, TON_BILGI, TON_TEKNIK
from pipeline import pipeline_calistir
from router import SmartRouter

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
BASE = f"https://api.telegram.org/bot{TOKEN}"

PIPELINE_STEPS = [
    "🎥 Forensic video analizi", "🔎 Research / Fact Lock", "🧠 Editorial Brain",
    "🎙️ Reels Creative", "📝 Caption + Hashtag", "🧵 Threads", "🔍 QA kalite kontrol",
    "🎧 Autonoe TTS", "🎬 FFmpeg video render",
]
TON_MAP = {"eglence": TON_EGLENCE, "dengeli": TON_DENGELI, "bilgi": TON_BILGI, "teknik": TON_TEKNIK}
TON_LABELS = {"eglence": "🎭 Eğlence Ağırlıklı", "dengeli": "⚖️ Dengeli", "bilgi": "🧠 Bilgi Ağırlıklı", "teknik": "📊 Teknik / Detaylı"}

def send_message(text):
    r = requests.post(f"{BASE}/sendMessage", data={"chat_id": CHAT_ID, "text": text}, timeout=60); r.raise_for_status(); return r.json()

def edit_message(message_id, text):
    r = requests.post(f"{BASE}/editMessageText", data={"chat_id": CHAT_ID, "message_id": message_id, "text": text}, timeout=60); r.raise_for_status()

def send_video(path, caption):
    with open(path, "rb") as fh:
        r = requests.post(f"{BASE}/sendVideo", data={"chat_id": CHAT_ID, "caption": caption}, files={"video": (Path(path).name, fh, "video/mp4")}, timeout=300)
    r.raise_for_status()

def video_duration(path):
    try:
        out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], text=True, timeout=30)
        return float(out.strip())
    except Exception: return None

def _format_title_options(titles):
    if not titles: return "🎯 BAŞLIK SEÇENEKLERİ\n\nBaşlık seçeneği üretilemedi."
    lines = ["🎯 BAŞLIK SEÇENEKLERİ", ""]
    for i, item in enumerate(titles, 1):
        if isinstance(item, dict):
            ana = str(item.get("ana", "")).strip()
            alt = str(item.get("alt", "")).strip()
            lines.append(f"{i}️⃣ {ana}")
            if alt: lines.append(f"   ↳ {alt}")
        else:
            lines.append(f"{i}️⃣ {str(item).strip()}")
    return "\n".join(lines)

def _loading_text(done, current=None, warnings=0, errors=0):
    total=len(PIPELINE_STEPS); filled=min(done,total); bar="█"*filled+"░"*(total-filled)
    lines=["⏳ REELS PIPELINE","",f"{bar}  {done}/{total}"]
    if current: lines += ["",f"🔄 {current}"]
    if warnings or errors: lines += ["",f"⚠️ Uyarı: {warnings}   ❌ Hata: {errors}"]
    return "\n".join(lines)

def _final_report(step_status,warnings,errors,result,tone_key):
    lines=["📊 PIPELINE RAPORU",""]
    for i,name in enumerate(PIPELINE_STEPS): lines.append(f"{step_status.get(i,'⚪')} {i+1}/9 {name}")
    lines += ["",f"🎯 İçerik türü: {TON_LABELS.get(tone_key,tone_key)}",f"🎙️ Ses: {result.get('secilen_ses_ingilizce') or 'Autonoe'}","⚡ TTS hız: 1.20x",f"🎚️ Senkron: {result.get('sync_note') or 'Süre kontrolü yapıldı'}",f"⚠️ Uyarı: {len(warnings)}",f"❌ Hata: {len(errors)}"]
    if warnings: lines += ["","⚠️ UYARILAR"]; lines.extend(f"• {x}" for x in warnings[:8])
    if errors: lines += ["","❌ HATALAR"]; lines.extend(f"• {x}" for x in errors[:8])
    return "\n".join(lines)[:4090]

def process(path):
    initial=send_message(_loading_text(0,"Video alındı, pipeline başlatılıyor...")); loading_id=initial["result"]["message_id"]
    raw=path.read_bytes(); duration=video_duration(path); mime=mimetypes.guess_type(path.name)[0] or "video/mp4"; router=SmartRouter(); step_status={}; warnings=[]; errors=[]
    tone_key=os.environ.get("CONTENT_TONE","dengeli").strip().lower(); tone_key=tone_key if tone_key in TON_MAP else "dengeli"; selected_tone=TON_MAP[tone_key]
    def log(msg):
        text=str(msg).strip(); print(text); lower=text.lower()
        if "⚠️" in text or "uyarı" in lower or "warning" in lower: warnings.append(text)
        if "❌" in text or "hata" in lower or "error" in lower: errors.append(text)
    def progress(n,total,msg):
        step_status[n-1]="🟢"; current=PIPELINE_STEPS[n-1] if 0<n<=len(PIPELINE_STEPS) else str(msg)
        try: edit_message(loading_id,_loading_text(n-1,f"Tamamlandı → {current}",len(warnings),len(errors)))
        except Exception as exc: print(f"Loading mesajı güncellenemedi: {exc}")
    try:
        result=pipeline_calistir(router=router,video_bytes=raw,mime_type=mime,temp_input_video=str(path),video_analiz_notlari="",metin_uretim_notlari="",sure_saniye=duration,icerik_tonu=selected_tone,secilen_ses_ingilizce="Autonoe",log_ekle=log,ilerlemeyi_guncelle=progress)
    except Exception as exc:
        errors.append(str(exc))
        try: edit_message(loading_id,_final_report(step_status,warnings,errors,{"secilen_ses_ingilizce":"Autonoe"},tone_key))
        except Exception: pass
        raise
    final=result.get("final_video")
    if not final or not Path(final).exists():
        errors.append("Pipeline tamamlandı ancak final video üretilemedi."); edit_message(loading_id,_final_report(step_status,warnings,errors,result,tone_key)); raise RuntimeError("Pipeline tamamlandı ancak final video üretilemedi.")
    result["sync_note"]=next((x for x in warnings if "senkron" in x.lower() or "süre uyumu" in x.lower()),"Süre kontrolü yapıldı")
    for i in range(len(PIPELINE_STEPS)): step_status.setdefault(i,"🟢")
    edit_message(loading_id,_final_report(step_status,warnings,errors,result,tone_key))
    caption=result.get("reels_aciklamasi") or ""; hashtags=result.get("reels_hashtagleri") or []
    if hashtags: caption += "\n\n"+" ".join("#"+str(x).lstrip("#") for x in hashtags)
    send_video(final,caption[:1024])
    send_message(_format_title_options(result.get("kapak_basliklari") or []))
    threads=result.get("threads_aciklamasi") or ""; send_message(f"THREADS AÇIKLAMASI\n\n{threads}" if threads else "THREADS AÇIKLAMASI\n\nAçıklama üretilemedi.")
    Path("pipeline_result.json").write_text(json.dumps({"source":path.name,"final_video":Path(final).name,"content_tone":tone_key,"seslendirme":result.get("seslendirme_metni",""),"caption":caption,"title_options":result.get("kapak_basliklari",[]),"threads":threads,"qa":result.get("qa_result",{}),"warnings":warnings,"errors":errors},ensure_ascii=False,indent=2),encoding="utf-8")

def main():
    raw=os.environ.get("VIDEO_FILES","").strip(); inputs=[Path(line.strip()) for line in raw.splitlines() if line.strip()]
    if not inputs: print("No new input videos from Telegram intake."); return
    for path in inputs:
        if not path.exists(): raise FileNotFoundError(f"Telegram intake output not found: {path}")
        print(f"Processing {path}"); process(path)

if __name__=="__main__":
    try: main()
    except Exception as exc:
        print(f"Pipeline worker error: {exc}",file=sys.stderr)
        try: send_message(f"❌ Pipeline hata verdi:\n\n{str(exc)[:3500]}")
        finally: raise
