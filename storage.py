"""Üretim kayıt yönetimi (yükle, kaydet, ekle, sil)."""
import os
import json
from datetime import datetime
from typing import List

from config import KAYIT_DOSYASI, MAX_KAYIT, guncel_tarih_metni

# ===== KAYIT YÖNETİMİ =====
def kayitlari_yukle() -> List[dict]:
    try:
        if os.path.exists(KAYIT_DOSYASI):
            with open(KAYIT_DOSYASI, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []

def kayitlari_kaydet(kayitlar: List[dict]) -> None:
    try:
        with open(KAYIT_DOSYASI, "w", encoding="utf-8") as f:
            json.dump(kayitlar, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def kayit_ekle(uretim_verisi: dict) -> None:
    kayitlar = kayitlari_yukle()
    kayit = {
        "tarih": f"{guncel_tarih_metni()} {datetime.now().strftime('%H:%M')}",
        "seslendirme_metni": uretim_verisi.get("seslendirme_metni", ""),
        "reels_aciklamasi": uretim_verisi.get("reels_aciklamasi", ""),
        "reels_hashtagleri": uretim_verisi.get("reels_hashtagleri", []),
        "kapak_basliklari": uretim_verisi.get("kapak_basliklari", []),
        "threads_aciklamasi": uretim_verisi.get("threads_aciklamasi", ""),
        "ses_adi": uretim_verisi.get("ses_adi", ""),
        "sure_saniye": uretim_verisi.get("sure_saniye", 30),
    }
    kayitlar.append(kayit)
    if len(kayitlar) > MAX_KAYIT:
        kayitlar = kayitlar[-MAX_KAYIT:]
    kayitlari_kaydet(kayitlar)

def tum_kayitlari_sil() -> None:
    try:
        if os.path.exists(KAYIT_DOSYASI):
            os.remove(KAYIT_DOSYASI)
    except Exception:
        pass
