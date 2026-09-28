"""Regression tests for shared retry budgets, SDK errors, and JSON shapes."""
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test-only-not-a-real-key")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.genai.errors import APIError
from jsonschema import Draft202012Validator

from core import router as router_module, schemas
from core.router import SmartRouter
from core.schema_validation import (
    StructuredOutputValidationError,
    validate_structured_output,
)


class RouterConcurrencyTests(unittest.TestCase):
    def test_parallel_wait_reservations_never_exceed_shared_budget(self):
        router = SmartRouter()
        workers = 12
        budget = 25.0
        barrier = threading.Barrier(workers)

        def reserve():
            barrier.wait(timeout=5)
            return router._bekleme_butcesi_rezerve_et(
                "_optional_overload_wait_spent", budget, 10.0,
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            reservations = list(pool.map(lambda _: reserve(), range(workers)))

        self.assertAlmostEqual(sum(reservations), budget)
        self.assertAlmostEqual(router._optional_overload_wait_spent, budget)
        self.assertTrue(all(value >= 0 for value in reservations))
        self.assertLessEqual(sum(value > 0 for value in reservations), 3)


class StructuredRouterErrorTests(unittest.TestCase):
    def setUp(self):
        self.router = SmartRouter()

    def test_sdk_status_fields_drive_classification(self):
        cases = (
            (APIError(404, {"error": {"status": "NOT_FOUND", "message": "Unknown model"}}), "model_key"),
            (APIError(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}), "quota_day"),
            (APIError(503, {"error": {"status": "UNAVAILABLE", "message": "High demand"}}), "unavailable"),
            (APIError(400, {"error": {"status": "INVALID_ARGUMENT", "message": "Unsupported config"}}), "model_config"),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.router._parse_hata(error)[0], expected)

    def test_transport_status_code_is_used_when_sdk_code_is_missing(self):
        error = SimpleNamespace(
            code=None,
            response=SimpleNamespace(status_code=504),
            status="",
            message="gateway deadline",
        )
        self.assertEqual(self.router._parse_hata(error)[0], "combo")

    def test_legacy_string_classification_remains_supported(self):
        self.assertEqual(self.router._parse_hata("503 Service Unavailable")[0], "unavailable")
        self.assertEqual(self.router._parse_hata("429 RESOURCE_EXHAUSTED quota")[0], "quota")


class RequestBudgetTests(unittest.TestCase):
    def test_required_profile_budget_stops_remaining_model_attempts(self):
        router = SmartRouter()
        router._ordered_api_items = lambda: [("k0", "fake-key")]
        calls = []

        class _Models:
            def generate_content(self, model, contents, config):
                calls.append(model)
                if model == "slow-first":
                    time.sleep(0.03)
                    raise RuntimeError("503 Service Unavailable")
                return SimpleNamespace(text='{"ok": true}')

        router.clients = {"k0": SimpleNamespace(models=_Models())}
        logs = []
        with patch.dict(router_module.ISTEK_PROFILLERI["metin"], {"butce_saniye": 0.005}):
            with self.assertRaises(RuntimeError):
                router._make_request(
                    ["slow-first", "must-not-run"], "input", None, logs.append,
                    require_text=True, profil="metin",
                )

        self.assertEqual(calls, ["slow-first"])
        self.assertTrue(any("toplam istek bütçesi" in item for item in logs))


class StructuredOutputValidationTests(unittest.TestCase):
    def test_repository_schemas_are_valid_json_schemas(self):
        for name, schema in vars(schemas).items():
            if name.endswith("_SCHEMA") and isinstance(schema, dict):
                with self.subTest(schema=name):
                    Draft202012Validator.check_schema(schema)

    def test_script_schema_accepts_valid_segments_and_rejects_wrong_speaker(self):
        valid = {
            "segments": [{"speaker": "female", "tts_tag": "[meraklı]", "text": "Merhaba."}],
            "yorum_tetikleyici_soru": "Siz ne düşünüyorsunuz?",
        }
        self.assertIs(validate_structured_output(valid, schemas.SCRIPT_WRITER_SCHEMA), valid)

        invalid = {
            "segments": [{"speaker": "private phrase", "tts_tag": "[meraklı]", "text": "Merhaba."}],
            "yorum_tetikleyici_soru": "Siz ne düşünüyorsunuz?",
        }
        with self.assertRaises(StructuredOutputValidationError) as raised:
            validate_structured_output(invalid, schemas.SCRIPT_WRITER_SCHEMA, "Script Writer")
        self.assertIn("segments.0.speaker", str(raised.exception))
        self.assertNotIn("private phrase", str(raised.exception))

    def test_router_validates_parsed_model_json_before_returning_it(self):
        router = SmartRouter.__new__(SmartRouter)
        payload = {
            "segments": [{"speaker": "host", "tts_tag": "[meraklı]", "text": "Merhaba."}],
            "yorum_tetikleyici_soru": "Siz ne düşünüyorsunuz?",
        }
        captured = {}

        def fake_request(*args, **kwargs):
            captured["validator"] = kwargs.get("response_validator")
            return SimpleNamespace(text=json.dumps(payload)), "fake+model"

        router._make_request = fake_request

        with self.assertRaises(StructuredOutputValidationError):
            router.metin_uret(
                "input", "prompt", schemas.SCRIPT_WRITER_SCHEMA,
                lambda _message: None, arama_kullan=False,
            )
        self.assertTrue(callable(captured["validator"]))

    def test_invalid_structured_response_falls_back_and_is_retried_next_request(self):
        router = SmartRouter()
        router._ordered_api_items = lambda: [("k0", "fake-key")]
        calls = []

        class _Models:
            def generate_content(self, model, contents, config):
                calls.append(model)
                if model == "bad-schema":
                    return SimpleNamespace(text='{"ok": "wrong type"}')
                return SimpleNamespace(text='{"ok": true}')

        router.clients = {"k0": SimpleNamespace(models=_Models())}
        schema = {
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        }

        def validator(response):
            parsed = json.loads(response.text)
            validate_structured_output(parsed, schema, "test response")

        for _ in range(2):
            response, model = router._make_request(
                ["bad-schema", "good-schema"], "input", None,
                lambda _message: None, require_text=True, response_validator=validator,
            )
            self.assertTrue(model.endswith("good-schema"))
            self.assertEqual(json.loads(response.text), {"ok": True})

        self.assertEqual(calls, ["bad-schema", "good-schema", "bad-schema", "good-schema"])
        self.assertFalse(any("wrong type" in item for item in router.blacklist))

    def test_router_returns_valid_model_json_unchanged(self):
        router = SmartRouter.__new__(SmartRouter)
        payload = {
            "segments": [{"speaker": "male", "tts_tag": "[vurgulu]", "text": "Tamam."}],
            "yorum_tetikleyici_soru": "Siz ne dersiniz?",
        }
        router._make_request = lambda *args, **kwargs: (SimpleNamespace(text=json.dumps(payload)), "fake+model")

        result, model = router.metin_uret(
            "input", "prompt", schemas.SCRIPT_WRITER_SCHEMA,
            lambda _message: None, arama_kullan=False,
        )
        self.assertEqual(result, payload)
        self.assertEqual(model, "fake+model")

    def test_video_analysis_response_is_validated(self):
        router = SmartRouter.__new__(SmartRouter)
        schema = {
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        }
        captured = {}

        def fake_request(*args, **kwargs):
            captured["validator"] = kwargs.get("response_validator")
            return SimpleNamespace(text='{"ok": "not-a-boolean"}'), "fake+video-model"

        router._make_request = fake_request

        with self.assertRaises(StructuredOutputValidationError):
            router.video_analiz_et(
                b"video", "video/mp4", "prompt", schema,
                lambda _message: None, model_listesi=["fake-model"],
            )
        self.assertTrue(callable(captured["validator"]))


if __name__ == "__main__":
    unittest.main()
