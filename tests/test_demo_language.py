"""Language field contract for POST /api/ask (inference, validation, passthrough)."""
from __future__ import annotations

import json
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
from server import Handler, ThreadingHTTPServer, infer_language, pipeline_accepts_language


class QuietHandler(Handler):
    def log_message(self, fmt, *args) -> None:
        pass


def _result(**extra) -> dict:
    return {"piece_id": "die-forelle", "answer": "ok", "evidence": [], **extra}


class InferLanguageTests(unittest.TestCase):
    def test_hangul_means_korean(self) -> None:
        self.assertEqual(infer_language("28마디부터 분위기 변화는?"), "ko")
        self.assertEqual(infer_language("How do I sing 송어?"), "ko")

    def test_no_hangul_means_english(self) -> None:
        self.assertEqual(infer_language("How should I phrase bars 28-30?"), "en")
        self.assertEqual(infer_language("Wie singe ich die Forelle?"), "en")

    def test_signature_detection(self) -> None:
        def with_language(*, piece_id, question, measure_range, generate, language="ko"):
            return {}

        def without_language(*, piece_id, question, measure_range, generate):
            return {}

        self.assertTrue(pipeline_accepts_language(with_language))
        self.assertFalse(pipeline_accepts_language(without_language))


class LanguageEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def post(self, payload: dict) -> tuple[int, dict]:
        request = Request(
            self.base_url + "/api/ask",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def body(self, question: str, **extra) -> dict:
        return {"piece_id": "die-forelle", "question": question, "generate": False, **extra}

    def test_language_is_passed_when_pipeline_supports_it(self) -> None:
        calls = []

        def fake_ask(*, piece_id, question, measure_range, generate, top_k=6, language="ko"):
            calls.append(language)
            return _result()

        with mock.patch.object(server.qa, "ask", fake_ask):
            for payload, expected in (
                (self.body("How should I breathe here?"), "en"),
                (self.body("어디서 숨을 쉬나요?"), "ko"),
                (self.body("어디서 숨을 쉬나요?", language="en"), "en"),
                (self.body("Where do I breathe?", language="ko"), "ko"),
            ):
                with self.subTest(payload=payload):
                    status, result = self.post(payload)
                    self.assertEqual(status, 200)
                    self.assertEqual(calls[-1], expected)
                    self.assertEqual(result["language"], expected)
                    self.assertEqual(result["requested_language"], expected)
                    self.assertTrue(result["language_supported"])

    def test_pipeline_reported_language_is_kept(self) -> None:
        def fake_ask(*, piece_id, question, measure_range, generate, language="ko"):
            return _result(language="ko")

        with mock.patch.object(server.qa, "ask", fake_ask):
            status, result = self.post(self.body("Where do I breathe?"))
        self.assertEqual(status, 200)
        self.assertEqual(result["language"], "ko")
        self.assertEqual(result["requested_language"], "en")

    def test_language_is_not_passed_to_older_pipelines(self) -> None:
        received = {}

        def legacy_ask(*, piece_id, question, measure_range, generate, top_k=6):
            received.update(piece_id=piece_id, question=question)
            return _result()

        with mock.patch.object(server.qa, "ask", legacy_ask):
            status, result = self.post(self.body("How should I breathe here?", language="en"))
        self.assertEqual(status, 200)
        self.assertEqual(received["question"], "How should I breathe here?")
        self.assertEqual(result["language"], "ko")
        self.assertEqual(result["requested_language"], "en")
        self.assertFalse(result["language_supported"])

    def test_invalid_language_is_rejected(self) -> None:
        def fake_ask(**kwargs):
            raise AssertionError("pipeline must not be called")

        with mock.patch.object(server.qa, "ask", fake_ask):
            for bad in ("fr", "EN", "", 1, True, ["en"]):
                with self.subTest(language=bad):
                    status, result = self.post(self.body("Where?", language=bad))
                    self.assertEqual(status, 400)
                    self.assertEqual(result["error"], 'language must be "ko" or "en"')

    def test_explicit_null_language_is_inferred(self) -> None:
        calls = []

        def fake_ask(*, piece_id, question, measure_range, generate, language="ko"):
            calls.append(language)
            return _result()

        with mock.patch.object(server.qa, "ask", fake_ask):
            status, _ = self.post(self.body("Where?", language=None))
        self.assertEqual(status, 200)
        self.assertEqual(calls, ["en"])


class StaticUiLabelTests(unittest.TestCase):
    def test_ui_labels_score_context_and_web_coaching(self) -> None:
        script = (server.STATIC_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("Lyrics in the selected measures", script)
        self.assertIn("LLM-assisted transcription", script)
        self.assertIn(
            "Answered from the score's lyrics only — no expert annotation covers these measures",
            script,
        )
        self.assertIn("Web / lesson video (not expert-curated)", script)

    def test_ui_labels_professional_soprano_apart_from_experts(self) -> None:
        script = (server.STATIC_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertRegex(script, r'ev\.kind === "sumijo"\s*\?\s*"Professional Soprano"')
        self.assertRegex(script, r'ev\.kind === "expert"\s*\?\s*"Expert annotation"')


if __name__ == "__main__":
    unittest.main()
