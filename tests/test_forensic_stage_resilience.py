"""2026-09-28 503 dalgası regresyon testleri (SDK içi retry çarpanı + stage kurtarma).

Üretim logunda kök neden ikiliydi:
  * google-genai SDK'sı TEK generate_content çağrısını dahili 5 denemeye kadar
    tekrarlıyordu (408/429/5xx'te ~1→60 sn üstel backoff). Router'ın kendi
    model×key×tur tekrarlarıyla üst üste binince her "deneme" 2-55 sn'e çıkıyor,
    video bütçesi (420 sn) TEK turda eriyor ve tasarlanan bekle+tekrar turları
    hiç çalışamıyordu (22 deneme = 456 sn).
  * Forensic video analizi (zorunlu ilk aşama) tüm kombinasyonlarda düşince
    istisna 9 aşamalık pipeline'ı ve Actions job'unu öldürüyordu.

Doğrulanan davranış:
  * Router istemcileri SDK içi retry'ı ROUTER_SDK_RETRY_ATTEMPTS (varsayılan 2)
    denemeye sınırlar; asıl tekrar stratejisi router'da kalır.
  * Geçici hatada (5xx/429/zaman aşımı/boş yanıt) aşama bekleyip tekrar denenir;
    kalıcı hatalarda (400/403/404) beklenmeden yükseltilir.
  * Aşama toplam süre sınırı aşılırsa yeniden deneme yapılmaz.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

import core.pipeline as pipeline_mod
import core.router as router_mod
from core.pipeline import (
    _forensic_analiz_calistir,
    _gecici_hata_mi,
    _gecici_hatada_tekrar_dene,
)
from core.router import SmartRouter


class _SunucuHatasi(Exception):
    """google.genai.errors.ServerError benzeri code taşıyan sahte hata."""

    def __init__(self, code):
        super().__init__(f"HTTP {code} hata")
        self.code = code


class SdkRetryLimitTests(unittest.TestCase):
    def test_router_clientlari_sdk_retry_sinirlar(self):
        """SDK içi retry varsayılan 5 değil, ROUTER_SDK_RETRY_ATTEMPTS olmalı."""
        yakalanan = []

        class _YakalayanClient:
            def __init__(self, api_key=None, http_options=None):
                self.http_options = http_options
                yakalanan.append(http_options)

        with patch.object(router_mod.genai, "Client", _YakalayanClient):
            SmartRouter()

        self.assertGreater(len(yakalanan), 0, "Hiç istemci oluşturulmadı")
        for opts in yakalanan:
            self.assertEqual(opts.retry_options.attempts, router_mod.SDK_RETRY_ATTEMPTS)
            self.assertLess(
                opts.retry_options.attempts, 5,
                "SDK içi retry varsayılan 5 denemeye geri döndü",
            )
            self.assertEqual(opts.timeout, router_mod.REQUEST_TIMEOUT_MS)

    def test_env_ile_retry_kapatilabilir(self):
        with patch.dict(os.environ, {"ROUTER_SDK_RETRY_ATTEMPTS": "1"}):
            self.assertEqual(
                router_mod._env_int("ROUTER_SDK_RETRY_ATTEMPTS", 2), 1
            )


class GeciciHataSinifiTestleri(unittest.TestCase):
    def test_gecici_kodlar(self):
        for code in (408, 429, 500, 502, 503, 504):
            self.assertTrue(_gecici_hata_mi(_SunucuHatasi(code)), msg=str(code))

    def test_kalici_kodlar(self):
        for code in (400, 403, 404):
            self.assertFalse(_gecici_hata_mi(_SunucuHatasi(code)), msg=str(code))

    def test_codesuz_hata_gecici_sayilir(self):
        # ValueError("Model boş yanıt verdi.") ve transport hataları code'suzdur.
        self.assertTrue(_gecici_hata_mi(ValueError("Model boş yanıt verdi.")))
        self.assertTrue(_gecici_hata_mi(ConnectionError("bağlantı koptu")))
        self.assertTrue(_gecici_hata_mi(TimeoutError("süre doldu")))


class _SleepYalitli(unittest.TestCase):
    """Gerçek 60 sn'lik beklemeyi engelleyen ortak _sleep yalıtımı."""

    def setUp(self):
        self.uyumalar = []
        patcher = patch.object(pipeline_mod, "_sleep", self.uyumalar.append)
        patcher.start()
        self.addCleanup(patcher.stop)


class StageRetryTestleri(_SleepYalitli):
    def test_gecici_hatada_bekle_ve_tekrar_dene(self):
        cagri = {"n": 0}

        def islev():
            cagri["n"] += 1
            if cagri["n"] == 1:
                raise _SunucuHatasi(503)
            return "analiz"

        sonuc = _gecici_hatada_tekrar_dene(islev, lambda _: None, "Test aşaması")
        self.assertEqual(sonuc, "analiz")
        self.assertEqual(cagri["n"], 2)
        self.assertEqual(len(self.uyumalar), 1)
        self.assertGreaterEqual(self.uyumalar[0], 5, "Bekleme süresi uygulanmalı")

    def test_kalici_hatada_beklemeden_yukseltir(self):
        cagri = {"n": 0}

        def islev():
            cagri["n"] += 1
            raise _SunucuHatasi(400)

        with self.assertRaises(_SunucuHatasi):
            _gecici_hatada_tekrar_dene(islev, lambda _: None, "Test aşaması")
        self.assertEqual(cagri["n"], 1, "Kalıcı hata tekrar denenmemeli")
        self.assertEqual(self.uyumalar, [], "Kalıcı hatada bekleme yapılmamalı")

    def test_sinir_asilirsa_tekrar_etmez(self):
        cagri = {"n": 0}

        def islev():
            cagri["n"] += 1
            raise _SunucuHatasi(503)

        with self.assertRaises(_SunucuHatasi):
            _gecici_hatada_tekrar_dene(
                islev, lambda _: None, "Test aşaması",
                ek_deneme=2, bekleme_saniye=60, toplam_sinir_saniye=30,
            )
        self.assertEqual(cagri["n"], 1, "Sınır aşılırsa ikinci deneme başlatılmamalı")
        self.assertEqual(self.uyumalar, [])

    def test_ek_deneme_tukenince_yukseltir(self):
        cagri = {"n": 0}

        def islev():
            cagri["n"] += 1
            raise _SunucuHatasi(503)

        with self.assertRaises(_SunucuHatasi):
            _gecici_hatada_tekrar_dene(
                islev, lambda _: None, "Test aşaması",
                ek_deneme=2, bekleme_saniye=1, toplam_sinir_saniye=1000,
            )
        self.assertEqual(cagri["n"], 3, "1 ilk + 2 ek deneme olmalı")
        self.assertEqual(len(self.uyumalar), 2)

    def test_hizli_basarisizlik_kapatilabilir(self):
        cagri = {"n": 0}

        def islev():
            cagri["n"] += 1
            raise _SunucuHatasi(503)

        with self.assertRaises(_SunucuHatasi):
            _gecici_hatada_tekrar_dene(
                islev, lambda _: None, "Test aşaması",
                ek_deneme=0, bekleme_saniye=60, toplam_sinir_saniye=780,
            )
        self.assertEqual(cagri["n"], 1)
        self.assertEqual(self.uyumalar, [])


class ForensicEntegrasyonTestleri(_SleepYalitli):
    def _log_topla(self):
        satirlar = []
        return satirlar.append, satirlar

    def test_forensic_gecici_hatada_kurtarir(self):
        yaz, satirlar = self._log_topla()
        cagri = {"n": 0}

        class _Router:
            def video_analiz_et(self, *_args, **_kwargs):
                cagri["n"] += 1
                if cagri["n"] == 1:
                    raise _SunucuHatasi(503)
                return {"observed_facts": ["araba"]}, "k0+model"

        sonuc, model = _forensic_analiz_calistir(_Router(), b"", "video/mp4", "", 60, yaz)
        self.assertEqual(cagri["n"], 2)
        self.assertEqual(model, "k0+model")
        self.assertTrue(any("tekrar deneniyor" in s for s in satirlar))

    def test_forensic_kalici_hatada_direkt_yukseltir(self):
        yaz, satirlar = self._log_topla()

        class _Router:
            def video_analiz_et(self, *_args, **_kwargs):
                raise _SunucuHatasi(404)

        with self.assertRaises(_SunucuHatasi):
            _forensic_analiz_calistir(_Router(), b"", "video/mp4", "", 60, yaz)
        # Kalıcı hata: bekleme yok, tek deneme, FAIL kaydı düşülmeli.
        self.assertFalse(any("tekrar deneniyor" in s for s in satirlar))
        self.assertTrue(any("FAIL" in s for s in satirlar))

    def test_forensic_basarili_yolda_ek_deneme_yok(self):
        yaz, _ = self._log_topla()

        class _Router:
            def __init__(self):
                self.cagrilar = 0

            def video_analiz_et(self, *_args, **_kwargs):
                self.cagrilar += 1
                return {"observed_facts": []}, "k0+model"

        router = _Router()
        _forensic_analiz_calistir(router, b"", "video/mp4", "", 60, yaz)
        self.assertEqual(router.cagrilar, 1)


if __name__ == "__main__":
    unittest.main()
