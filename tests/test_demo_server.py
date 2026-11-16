"""HTTP contract checks for the merged demo server."""
from __future__ import annotations

import json
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
from server import Handler, ThreadingHTTPServer
from soprano_qa.dense import DenseRetrievalUnavailable
from soprano_qa.llm import GenerationBackendUnavailable

MOOD_QUERY = "28마디부터 분위기 변화를 어떻게 표현해야 하나요?"
MOOD_UNIT_ID = "die-forelle-ku-010"
SECOND_BEAT_ACCENT_UNIT_ID = "die-forelle-ku-002"


class QuietHandler(Handler):
    def log_message(self, fmt, *args) -> None:
        pass


class DemoServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever,
            daemon=True,
        )
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def post_raw(self, payload: bytes) -> tuple[int, dict]:
        request = Request(
            self.base_url + "/api/ask",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_non_object_json_bodies_are_client_errors(self) -> None:
        for payload in (b"[]", b"null", b'"question"'):
            with self.subTest(payload=payload):
                status, result = self.post_raw(payload)
                self.assertEqual(status, 400)
                self.assertEqual(
                    result["error"],
                    "request body must be a JSON object",
                )

    def test_demo_labels_each_kind_of_answer(self) -> None:
        with urlopen(self.base_url + "/static/app.js", timeout=10) as response:
            script = response.read().decode("utf-8")

        for label in ("LLM · with sources", "General music explanation",
                      "Score lyrics only", "Outside music scope", "Answer unavailable"):
            self.assertIn(label, script)

    def test_http_endpoint_uses_pipeline_retrieval(self) -> None:
        status, result = self.post_raw(
            json.dumps(
                {
                    "piece_id": "die-forelle",
                    "question": MOOD_QUERY,
                    "measure_range": [28, 30],
                    "generate": False,
                }
            ).encode("utf-8")
        )

        self.assertEqual(status, 200, msg=result)
        self.assertEqual(result["pipeline"], "soprano_qa")
        self.assertEqual(result["evidence"][0]["id"], MOOD_UNIT_ID)
        self.assertEqual(result["language"], "ko")
        self.assertTrue(result["language_supported"])
        self.assertEqual(
            [measure["measure"] for measure in result["score_context"]["measures"]],
            [28],
        )

    def test_http_endpoint_retrieves_reviewed_second_beat_accent_unit(self) -> None:
        status, result = self.post_raw(
            json.dumps(
                {
                    "piece_id": "die-forelle",
                    "question": (
                        "피아노 반주에서 두 번째 박의 악센트는 무엇을 나타내는가?"
                    ),
                    "measure_range": [2, 5],
                    "generate": False,
                }
            ).encode("utf-8")
        )

        self.assertEqual(status, 200, msg=result)
        self.assertEqual(result["evidence"][0]["id"], SECOND_BEAT_ACCENT_UNIT_ID)
        self.assertIn("송어가 뛰어노는 모습", result["answer"])

    def ask_body(self, **extra) -> bytes:
        return json.dumps(
            {
                "piece_id": "die-forelle",
                "question": MOOD_QUERY,
                "measure_range": [28, 30],
                "generate": True,
                **extra,
            }
        ).encode("utf-8")

    def test_unavailable_backends_are_service_unavailable(self) -> None:
        for failure in (
            GenerationBackendUnavailable("generation checkpoint missing"),
            DenseRetrievalUnavailable("embedding backend missing"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(server.qa, "ask", side_effect=failure):
                    status, result = self.post_raw(self.ask_body())
                self.assertEqual(status, 503)
                self.assertEqual(result["error"], str(failure))

    def test_corpus_stats_backend_unavailable_is_service_unavailable(self) -> None:
        failure = DenseRetrievalUnavailable("embedding backend missing")
        with mock.patch.object(server.qa, "corpus_stats", side_effect=failure):
            try:
                with urlopen(self.base_url + "/api/corpus_stats", timeout=10) as response:
                    status, result = response.status, json.load(response)
            except HTTPError as exc:
                status, result = exc.code, json.load(exc)
        self.assertEqual(status, 503)
        self.assertEqual(result["error"], str(failure))

    def test_answer_model_failure_is_service_unavailable(self) -> None:
        from soprano_qa.llm import GenerationFailed

        with mock.patch.object(server.qa, "ask", side_effect=GenerationFailed("timeout")):
            status, result = self.post_raw(self.ask_body())
        self.assertEqual(status, 503)
        self.assertEqual(result["error"], server.REMOTE_MODEL_UNAVAILABLE)

    def test_question_measures_outside_selection_are_client_errors(self) -> None:
        with mock.patch.object(server.qa, "ask") as ask:
            status, result = self.post_raw(
                self.ask_body(question="40마디는 어떻게 부르나요?")
            )
        self.assertEqual(status, 400)
        self.assertIn("outside", result["error"])
        ask.assert_not_called()

    def test_pipeline_value_errors_are_not_reported_as_client_errors(self) -> None:
        failure = ValueError("requested tokens exceed the context window")
        with (
            mock.patch.object(server.qa, "ask", side_effect=failure),
            mock.patch.object(server.traceback, "print_exc"),
        ):
            status, result = self.post_raw(self.ask_body())
        self.assertEqual(status, 500)
        self.assertEqual(result["error"], "internal error")


if __name__ == "__main__":
    unittest.main()
