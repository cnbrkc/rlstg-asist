"""Cloudflare webhook güvenlik sözleşmesi (#21/#22):
  * /setup kimlik doğrulamasız webhook'u yeniden yazamaz (drop_pending_updates DoS)
  * sohbet allowlist'i yapılandırılmışsa yabancı sohbet pipeline başlatamaz
  * update_id path'e girmeden doğrulanır
  * pending save/keyboard hatası yakalanır → Telegram sonsuz yeniden teslim
    döngüsüne girmez, kullanıcıya haber verilir
"""
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

KOK = Path(__file__).resolve().parents[1]
JS = KOK / "cloudflare/telegram-webhook.js"


class CloudflareWebhookSozlesmeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kaynak = JS.read_text(encoding="utf-8")

    def test_setup_endpoointi_gizli_anahtar_ister(self):
        self.assertIn('url.pathname === "/setup"', self.kaynak)
        self.assertIn('url.searchParams.get("key") !== env.TELEGRAM_WEBHOOK_SECRET', self.kaynak)
        # authsuz 401 dönmeli
        self.assertLess(
            self.kaynak.index('pathname === "/setup"'),
            self.kaynak.index("setWebhook"),
            "/setup setWebhook'tan ÖNCE doğrulamalı",
        )

    def test_sohbet_allowlist_zorlanir(self):
        self.assertIn("function allowedChats(env)", self.kaynak)
        self.assertIn("function chatAllowed(env, chatId)", self.kaynak)
        self.assertIn("if (!chatAllowed(env, chatId))", self.kaynak)
        # Yabancı sohbete yanıt döner ama pipeline kuyruğa ALINMAZ.
        self.assertIn('"Forbidden"', self.kaynak)
        self.assertIn("ALLOWED_CHAT_IDS", self.kaynak)

    def test_update_id_path_ten_once_dogrulanir(self):
        self.assertIn(r"/^\d{1,32}$/.test(updateId)", self.kaynak)
        # Doğrulama, pendingPath'in inşasından önce gelmeli.
        self.assertLess(
            self.kaynak.index(r"/^\d{1,32}$/.test(updateId)"),
            self.kaynak.index("const pendingPath"),
        )

    def test_pending_save_hatasi_yakalanir_ve_kullanici_bilgilendirilir(self):
        self.assertIn("Pending save/keyboard failed", self.kaynak)
        self.assertIn("Girdi işlenirken hata oluştu", self.kaynak)
        # githubPut artık try bloğu içinde olmalı
        try_idx = self.kaynak.index("try {\n      const pendingPath")
        put_idx = self.kaynak.index("await githubPut(env, pendingPath")
        catch_idx = self.kaynak.index("} catch (error) {", try_idx)
        self.assertTrue(try_idx < put_idx < catch_idx, "githubPut try/catch dışında")


class CloudflareJsSyntaxTests(unittest.TestCase):
    def test_node_check_gecer(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node yok; sözdizimi denetimi atlandı")
        sonuc = subprocess.run([node, "--check", str(JS)], capture_output=True, text=True)
        self.assertEqual(0, sonuc.returncode, f"node --check başarısız: {sonuc.stderr[:400]}")


if __name__ == "__main__":
    unittest.main()
