"""Duo Autonoe + Charon ses timeline katmanı.

Mevcut tek sesli TTS akışından bağımsız çalışır. Her konuşma segmentini
karakterinin Gemini voice'u ile üretir, mevcut SES_HIZ_CARPANI ile hızlandırır
ve WAV segmentlerini tek bir 48 kHz mono PCM timeline'da birleştirir.

Duo segmentleri birbirinden bağımsız olduğu için TTS istekleri kontrollü
paralellik ile üretilir. Konuşma sırası değişmez; yalnızca toplam bekleme
süresi azalır.
"""
import os
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed

from character_profiles import voice_for_character
from media import gecici_ses_yolu, temp_dosya_temizle
from config import SES_HIZ_CARPANI

# 3 eşzamanlı istek, API tarafında gereksiz burst/rate-limit riskini artırmadan
# 5 segmentlik Duo'nun seri üretimine göre ciddi wall-clock kazancı sağlar.
DUO_TTS_MAX_WORKERS = 3


def _wav_format(path: str):
    with wave.open(path, "rb") as wf:
        return wf.getnchannels(), wf.getsampwidth(), wf.getframerate()


def _wavleri_birlestir(paths, output):
    if not paths:
        return False
    first = paths[0]
    params = None
    try:
        with wave.open(first, "rb") as wf:
            params = wf.getparams()
            frames = [wf.readframes(wf.getnframes())]
        for path in paths[1:]:
            with wave.open(path, "rb") as wf:
                current = wf.getparams()
                if (current.nchannels, current.sampwidth, current.framerate) != (
                    params.nchannels, params.sampwidth, params.framerate
                ):
                    raise ValueError("Duo WAV segment formatları eşleşmiyor.")
                frames.append(wf.readframes(wf.getnframes()))
        with wave.open(output, "wb") as out:
            out.setparams(params)
            for chunk in frames:
                out.writeframes(chunk)
        return os.path.exists(output) and os.path.getsize(output) > 44
    except Exception:
        return False


def _duo_segment_uret(router, index, total, segment, log_ekle, hiz_carpani):
    speaker = str(segment.get("speaker", "")).strip().lower()
    if speaker not in ("female", "male"):
        raise ValueError(f"Geçersiz Duo speaker: {speaker}")
    voice = voice_for_character(speaker)
    path = gecici_ses_yolu()
    log_ekle(f"🎙️ Duo {index}/{total}: {speaker} → {voice}")
    ok, info = router.ses_uret(
        str(segment.get("text", "")).strip(),
        voice,
        path,
        log_ekle,
        hiz_carpani=hiz_carpani,
    )
    if not ok or not os.path.exists(path):
        raise RuntimeError(f"Duo TTS segmenti üretilemedi: {speaker}/{voice}")
    return index, path, info


def duo_ses_uret(router, segments, output_path, log_ekle, hiz_carpani=SES_HIZ_CARPANI):
    """Validated speaker segmentlerinden tek WAV timeline üretir.

    Başarısız herhangi bir segmentte bütün Duo denemesi başarısız sayılır;
    caller legacy tek sesli akışa güvenli biçimde dönebilir.

    Segment TTS çağrıları kontrollü olarak paralel yapılır. Son WAV birleştirme
    her zaman orijinal segment sırasını korur; Autonoe/Charon eşleşmesi değişmez.
    """
    valid = [s for s in (segments or []) if isinstance(s, dict) and str(s.get("text", "")).strip()]
    if not valid:
        return False, None, []

    temp_segments = []
    results = {}
    total = len(valid)
    workers = min(DUO_TTS_MAX_WORKERS, total)

    try:
        # Her segment bağımsız bir Gemini TTS çağrısıdır. Paralel üretim toplam
        # wall-clock süresini düşürür; sonuçlar aşağıda index'e göre sıralanır.
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="duo-tts") as pool:
            futures = [
                pool.submit(_duo_segment_uret, router, index, total, segment, log_ekle, hiz_carpani)
                for index, segment in enumerate(valid, 1)
            ]
            for future in as_completed(futures):
                index, path, info = future.result()
                results[index] = (path, info)

        ordered_paths = [results[index][0] for index in range(1, total + 1)]
        infos = [results[index][1] for index in range(1, total + 1)]
        temp_segments.extend(ordered_paths)

        if not _wavleri_birlestir(ordered_paths, output_path):
            raise RuntimeError("Duo WAV segmentleri birleştirilemedi.")
        if not os.path.exists(output_path) or os.path.getsize(output_path) <= 44:
            raise RuntimeError("Duo birleşik WAV dosyası geçersiz.")

        return True, "+".join(x for x in infos if x), temp_segments
    except Exception as exc:
        log_ekle(f"⚠️ Duo TTS başarısız; legacy tek sesli akış kullanılacak: {str(exc)[:180]}")
        temp_dosya_temizle(output_path)
        return False, None, temp_segments
