"""Yedek arama sağlayıcıları + katman sırası (04.10.2026 arama hatası).

Kök neden: ddgs "auto" arka uçları Türkçe/çok terimli sorgularda "No results
found" döndürüyordu ve tek sağlayıcıya bağlı arama kör kalıyordu. Bu testler
ağ erişimi olmadan (mocked requests/ddgs) şunları güvenceye alır:

  1. duckduckgo_sorgu katman sırası: auto → ddgs arka uç rotasyonu → haber →
     HTTP yedekleri; "No results found" gerçek boşluktur, hata değildir.
  2. HTTP yedek sağlayıcıları (DDG HTML / Mojeek / Google News / Wikipedia)
     HTML/RSS/JSON ayrıştırması; hiçbiri istisna sızdırmaz.
  3. Bölge varsayılanı tr-tr.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

import core.arama_yedekleri as yedek
import core.web_search as web_search


class _Yanit:
    def __init__(self, text="", ok=True, payload=None, hata=None):
        self.text = text
        self.ok = ok
        self._payload = payload
        self._hata = hata

    def json(self):
        if self._hata:
            raise self._hata
        return self._payload


class BolgeTests(unittest.TestCase):
    def test_varsayilan_tr_tr(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DDGS_REGION", None)
            self.assertEqual("tr-tr", yedek.bolge())

    def test_gecersiz_bolge_varsayilana_doner(self):
        with patch.dict(os.environ, {"DDGS_REGION": "turkce"}):
            self.assertEqual("tr-tr", yedek.bolge())


class DdgHtmlTests(unittest.TestCase):
    HTML = (
        '<div class="result results_links">'
        '<a rel="nofollow" class="result__a" '
        'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fhaber&amp;rut=1">'
        "Kia Seltos fiyat</a>"
        '<a class="result__snippet" href="x">Seltos Türkiye\'de 2.349.000 TL.</a>'
        "</div>"
        '<div class="result results_links">'
        '<a rel="nofollow" class="result__a" href="https://b.com/x">İkinci başlık</a>'
        '<td class="result__snippet">İkinci özet.</td>'
        "</div>"
    )

    def test_sonuclar_ve_yonlendirme_cozulur(self):
        with patch.object(yedek, "requests", create=True) as sahte:
            sahte.post.return_value = _Yanit(self.HTML)
            sonuclar = yedek._ddg_html_sorgu("Kia Seltos fiyat", 5)
        self.assertEqual(2, len(sonuclar))
        self.assertEqual("Kia Seltos fiyat", sonuclar[0]["baslik"])
        self.assertIn("2.349.000 TL", sonuclar[0]["icerik"])
        self.assertEqual("https://example.com/haber", sonuclar[0]["kaynak"])
        self.assertEqual("https://b.com/x", sonuclar[1]["kaynak"])

    def test_hata_durumunda_bos_doner(self):
        sahte = type("S", (), {"post": staticmethod(lambda *a, **k: _Yanit(ok=False))})()
        with patch.object(yedek, "requests", sahte):
            self.assertEqual([], yedek._ddg_html_sorgu("x", 5))


class MojeekTests(unittest.TestCase):
    HTML = (
        '<a class="ob" href="https://www.mojeek.com/x">Mojeek başlık</a>'
        '<p class="s">Mojeek özeti.</p>'
    )

    def test_parcalanir(self):
        sahte = type("S", (), {"get": staticmethod(lambda *a, **k: _Yanit(self.HTML))})()
        with patch.object(yedek, "requests", sahte):
            sonuclar = yedek._mojeek_sorgu("x", 5)
        self.assertEqual(1, len(sonuclar))
        self.assertEqual("Mojeek başlık", sonuclar[0]["baslik"])
        self.assertEqual("Mojeek özeti.", sonuclar[0]["icerik"])


class GoogleNewsTests(unittest.TestCase):
    RSS = (
        "<rss><channel>"
        "<item><title>Kia Seltos zam</title><link>https://haber.com/1</link>"
        "<description><![CDATA[<b>Seltos</b> fiyatı güncellendi.]]></description></item>"
        "<item><title>İkinci haber</title><link>https://haber.com/2</link>"
        "<description>Özet</description></item>"
        "</channel></rss>"
    )

    def test_rss_parcalanir(self):
        sahte = type("S", (), {"get": staticmethod(lambda *a, **k: _Yanit(self.RSS))})()
        with patch.object(yedek, "requests", sahte):
            sonuclar = yedek._google_haber_sorgu("Kia Seltos", 5)
        self.assertEqual(2, len(sonuclar))
        self.assertEqual("Kia Seltos zam", sonuclar[0]["baslik"])
        self.assertEqual("Seltos fiyatı güncellendi.", sonuclar[0]["icerik"])


class BingRssTests(unittest.TestCase):
    RSS = (
        "<rss><channel><item><title>Kia Seltos Türkiye fiyat</title>"
        "<link>https://bing.com/x</link><description>Seltos fiyatı 2.349.000 TL.</description>"
        "</item></channel></rss>"
    )

    def test_bing_rss_parcalanir(self):
        sahte = type("S", (), {"get": staticmethod(lambda *a, **k: _Yanit(self.RSS))})()
        with patch.object(yedek, "requests", sahte):
            sonuclar = yedek._bing_rss_sorgu("Kia Seltos fiyat", 5)
        self.assertEqual(1, len(sonuclar))
        self.assertEqual("Kia Seltos Türkiye fiyat", sonuclar[0]["baslik"])
        self.assertEqual("https://bing.com/x", sonuclar[0]["kaynak"])


class WikipediaTests(unittest.TestCase):
    def test_ilgisiz_madde_elenir(self):
        payload = {"query": {"search": [
            {"title": "Kia Seltos", "snippet": "Seltos bir <b>SUV</b> modelidir."},
            {"title": "Kediler", "snippet": "Kediler evcil hayvandır."},
        ]}}
        sahte = type("S", (), {"get": staticmethod(lambda *a, **k: _Yanit(payload=payload))})()
        with patch.object(yedek, "requests", sahte):
            sonuclar = yedek._wikipedia_sorgu("Kia Seltos motor hacmi", 5)
        self.assertEqual(["Kia Seltos"], [s["baslik"] for s in sonuclar])
        self.assertIn("SUV", sonuclar[0]["icerik"])

    def test_bozuk_json_istisna_sizdirilmaz(self):
        sahte = type("S", (), {"get": staticmethod(lambda *a, **k: _Yanit(hata=ValueError("bozuk")))})()
        with patch.object(yedek, "requests", sahte):
            self.assertEqual([], yedek._wikipedia_sorgu("x", 5))


class HttpYedekSorguTests(unittest.TestCase):
    def test_ilk_sonuclu_saglayici_kazanir_ve_loglanir(self):
        def patlayan(sorgu, max_sonuc):
            raise RuntimeError("ağ yok")

        def calisan(sorgu, max_sonuc):
            return [{"baslik": "b", "icerik": "i", "kaynak": "k"}]

        logs = []
        with patch.object(yedek, "_SAGLAYICILAR", (("a", patlayan), ("b", calisan))):
            sonuclar, hatalar = yedek.http_yedek_sorgu_detayli("Kia Seltos", 4, logs.append)
        self.assertEqual(1, len(sonuclar))
        self.assertTrue(any(h.startswith("a:") for h in hatalar))
        self.assertTrue(any("yedek kaynak" in l for l in logs))

    def test_tum_saglayicilar_bossa_bos_liste(self):
        with patch.object(yedek, "_SAGLAYICILAR", (("a", lambda s, m: []), ("b", lambda s, m: []))):
            sonuclar, hatalar = yedek.http_yedek_sorgu_detayli("x", 4, lambda m: None)
        self.assertEqual([], sonuclar)
        self.assertEqual([], hatalar)

    def test_requests_yoksa_cokme_yok(self):
        with patch.object(yedek, "requests", None):
            sonuclar, hatalar = yedek.http_yedek_sorgu_detayli("x", 4, lambda m: None)
        self.assertEqual([], sonuclar)
        self.assertEqual(["requests-yok"], hatalar)

    def test_http_yedek_sorgu_detay_siz_sarmalayici(self):
        with patch.object(yedek, "_SAGLAYICILAR", (("a", lambda s, m: [{"baslik": "b", "icerik": "i", "kaynak": ""}]),)):
            sonuclar = yedek.http_yedek_sorgu("x", 4, lambda m: None)
        self.assertEqual(1, len(sonuclar))


class _SahteDDGS:
    """DDGS yerine geçen sınıf: sorgu/backend/kategori bazlı sonuç döndürür."""

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def text(self, sorgu, **kwargs):
        type(self).cagrilar.append(("text", sorgu, kwargs))
        return type(self)._sonuc(sorgu, kwargs)

    def news(self, sorgu, **kwargs):
        type(self).cagrilar.append(("news", sorgu, kwargs))
        return type(self)._sonuc(sorgu, kwargs)


def _sahte_ddgs(sonuc_fonksiyonu):
    class _DDGS(_SahteDDGS):
        cagrilar = []
        _sonuc = staticmethod(sonuc_fonksiyonu)

    _DDGS.cagrilar = []
    return _DDGS


class KatmanSirasiTests(unittest.TestCase):
    def test_no_results_gercek_bosluktur_hata_bildirilmez(self):
        """ddgs 'No results found.' derse yedeklere geçilir; istek hatası sayılmaz."""
        class _BosDDGS(_SahteDDGS):
            def text(self, sorgu, **kwargs):
                raise RuntimeError("No results found.")

            def news(self, sorgu, **kwargs):
                raise RuntimeError("No results found.")

        hatalar = []
        logs = []
        with patch.object(web_search, "_ddgs_sinifi", return_value=_BosDDGS), \
             patch.object(web_search, "http_yedek_sorgu_detayli", return_value=([], [])):
            sonuclar = web_search.duckduckgo_sorgu("Kia Seltos motor hacmi cc", 4, logs.append, lambda: hatalar.append(1))
        self.assertEqual([], sonuclar)
        self.assertEqual([], hatalar)
        self.assertTrue(any("sonuçsuz" in l for l in logs))

    def test_hiz_siniri_hatasi_bildirilir(self):
        class _LimitDDGS(_SahteDDGS):
            def text(self, sorgu, **kwargs):
                raise RuntimeError("RatelimitException: 429")

            def news(self, sorgu, **kwargs):
                raise RuntimeError("RatelimitException: 429")

        hatalar = []
        with patch.object(web_search, "_ddgs_sinifi", return_value=_LimitDDGS), \
             patch.object(web_search, "http_yedek_sorgu_detayli", return_value=([], ["mojeek:Timeout"])):
            sonuclar = web_search.duckduckgo_sorgu("Kia Seltos", 4, lambda m: None, lambda: hatalar.append(1))
        self.assertEqual([], sonuclar)
        self.assertEqual([1], hatalar)

    def test_arka_uc_rotasyonu_bos_sonucta_devreye_girer(self):
        def sonuc(sorgu, kwargs):
            if kwargs.get("backend"):
                return [{"title": "Rotasyon sonucu", "body": "Bing/brave yedek metni.", "href": "https://b"}]
            return []

        DDGS = _sahte_ddgs(sonuc)
        logs = []
        with patch.object(web_search, "_ddgs_sinifi", return_value=DDGS), \
             patch.object(web_search, "http_yedek_sorgu_detayli", return_value=([], [])):
            sonuclar = web_search.duckduckgo_sorgu("Kia Seltos", 4, logs.append)
        self.assertEqual(1, len(sonuclar))
        self.assertEqual("Rotasyon sonucu", sonuclar[0]["baslik"])
        self.assertTrue(any("arka uç rotasyonu" in l for l in logs))
        self.assertTrue(any(k[2].get("backend") for k in DDGS.cagrilar))

    def test_haber_rotasyonu_devreye_girer(self):
        def sonuc(sorgu, kwargs):
            return []

        DDGS = _sahte_ddgs(sonuc)

        class _HaberDDGS(_SahteDDGS):
            def text(self, sorgu, **kwargs):
                return []

            def news(self, sorgu, **kwargs):
                return [{"title": "Haber", "body": "Haber özeti.", "url": "https://n"}]

        with patch.object(web_search, "_ddgs_sinifi", return_value=_HaberDDGS), \
             patch.object(web_search, "http_yedek_sorgu_detayli", return_value=([], [])):
            sonuclar = web_search.duckduckgo_sorgu("Kia Seltos ÖTV", 4, lambda m: None)
        self.assertEqual(1, len(sonuclar))
        self.assertEqual("Haber", sonuclar[0]["baslik"])

    def test_http_yedek_katmani_devreye_girer(self):
        def sonuc(sorgu, kwargs):
            return []

        DDGS = _sahte_ddgs(sonuc)
        with patch.object(web_search, "_ddgs_sinifi", return_value=DDGS), \
             patch.object(
                 web_search, "http_yedek_sorgu_detayli",
                 return_value=([{"baslik": "HTTP yedek", "icerik": "Özet", "kaynak": "https://x"}], []),
             ):
            sonuclar = web_search.duckduckgo_sorgu("Kia Seltos fiyat", 4, lambda m: None)
        self.assertEqual("HTTP yedek", sonuclar[0]["baslik"])
        self.assertEqual("https://x", sonuclar[0]["kaynak"])

    def test_bolge_tr_tr_gonderilir(self):
        def sonuc(sorgu, kwargs):
            return [{"title": "t", "body": "b", "href": "https://x"}]

        DDGS = _sahte_ddgs(sonuc)
        with patch.dict(os.environ, {}, clear=False), \
             patch.object(web_search, "_ddgs_sinifi", return_value=DDGS):
            os.environ.pop("DDGS_REGION", None)
            web_search.duckduckgo_sorgu("Kia Seltos", 4, lambda m: None)
        self.assertTrue(DDGS.cagrilar)
        self.assertTrue(all(k[2].get("region") == "tr-tr" for k in DDGS.cagrilar))


class SorguKisaligiTests(unittest.TestCase):
    def test_sorgular_kisa_ve_yer_tutucusuz(self):
        from core.web_search import arastirma_sorgulari_olustur

        state = {
            "video_identity": {"brand": "Kia", "exact_model": "Seltos", "variant": "UNKNOWN"},
            "viral_arastirma_ihtiyaclari": [
                "Türkiye'deki Kia Seltos UNKNOWN fiyatlandırması nasıl olacak?",
            ],
        }
        sorgular = arastirma_sorgulari_olustur(state)
        self.assertTrue(sorgular)
        for sorgu in sorgular:
            self.assertLessEqual(len(sorgu.split()), 8, sorgu)
            self.assertNotIn("UNKNOWN", sorgu.upper())
        self.assertEqual(len(sorgular), len(set(sorgular)))
        # Aynı model adı iki kez geçmemeli (Kia Seltos Türkiye'deki Kia Seltos...).
        dolu = [s for s in sorgular if s.count("Seltos") > 1]
        self.assertEqual([], dolu)


if __name__ == "__main__":
    unittest.main()
