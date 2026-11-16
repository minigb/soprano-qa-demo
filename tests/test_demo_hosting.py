"""Settings for serving the API to the static page on another origin."""
from __future__ import annotations

import io
import json
import os
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
from server import AskLimiter, Handler, ThreadingHTTPServer

PAGES_ORIGIN = "https://minigb.github.io"
ANSWER = {"answer": "ok", "evidence": [], "language": "ko"}


class QuietHandler(Handler):
    def log_message(self, fmt, *args) -> None:
        pass


class AskLimiterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1000.0
        self.clock = lambda: self.now

    def test_unset_limits_allow_everything(self) -> None:
        limiter = AskLimiter(clock=self.clock)
        for _ in range(100):
            self.assertIsNone(limiter.acquire("a"))

    def test_per_client_window(self) -> None:
        limiter = AskLimiter(per_client=(2, 60), clock=self.clock)
        self.assertIsNone(limiter.acquire("a"))
        self.assertIsNone(limiter.acquire("a"))
        scope, wait = limiter.acquire("a")
        self.assertEqual(scope, "client")
        self.assertAlmostEqual(wait, 60)
        self.assertIsNone(limiter.acquire("b"))
        self.now += 61
        self.assertIsNone(limiter.acquire("a"))

    def test_daily_limit_is_shared(self) -> None:
        limiter = AskLimiter(daily=2, clock=self.clock)
        self.assertIsNone(limiter.acquire("a"))
        self.assertIsNone(limiter.acquire("b"))
        self.assertEqual(limiter.acquire("c")[0], "daily")
        self.now += 86401
        self.assertIsNone(limiter.acquire("c"))

    def test_rate_parsing(self) -> None:
        self.assertEqual(server._parse_rate("6/600"), (6, 600.0))
        self.assertIsNone(server._parse_rate(""))


class ServerTestCase(unittest.TestCase):
    """A server on a free port, with the page's origin allowed."""

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

    def setUp(self) -> None:
        env = mock.patch.dict(
            os.environ, {"SOPRANO_LAB_ALLOWED_ORIGINS": PAGES_ORIGIN + "/"}
        )
        env.start()
        self.addCleanup(env.stop)

    def request(self, method, path, body=None, headers=None):
        request = Request(
            self.base_url + path,
            data=body,
            headers={"Content-Type": "application/json", **(headers or {})},
            method=method,
        )
        try:
            with urlopen(request, timeout=10) as response:
                raw = response.read()
                return response.status, response.headers, raw
        except HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def ask(self, origin=PAGES_ORIGIN, **overrides):
        body = {"piece_id": "die-forelle", "question": "어떻게 부르나요?", **overrides}
        return self.request(
            "POST", "/api/ask", json.dumps(body).encode("utf-8"), {"Origin": origin}
        )


class HostingHttpTests(ServerTestCase):
    def test_allowed_origin_gets_cors_headers(self) -> None:
        with mock.patch.object(server.qa, "ask", return_value=dict(ANSWER)):
            status, headers, _ = self.ask()
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], PAGES_ORIGIN)

    def test_other_origins_get_no_cors_headers(self) -> None:
        with mock.patch.object(server.qa, "ask", return_value=dict(ANSWER)):
            _, headers, _ = self.ask(origin="https://example.com")
        self.assertIsNone(headers["Access-Control-Allow-Origin"])

    def test_preflight(self) -> None:
        status, headers, _ = self.request(
            "OPTIONS", "/api/ask", headers={
                "Origin": PAGES_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], PAGES_ORIGIN)
        self.assertIn("POST", headers["Access-Control-Allow-Methods"])
        self.assertIn("Content-Type", headers["Access-Control-Allow-Headers"])

    def test_rate_limited_questions_get_429(self) -> None:
        limiter = AskLimiter(per_client=(1, 600))
        with (
            mock.patch.object(server, "ASK_LIMITER", limiter),
            mock.patch.object(server.qa, "ask", return_value=dict(ANSWER)) as ask,
        ):
            self.assertEqual(self.ask()[0], 200)
            status, headers, raw = self.ask()
        self.assertEqual(status, 429)
        self.assertEqual(headers["Access-Control-Allow-Origin"], PAGES_ORIGIN)
        self.assertTrue(int(headers["Retry-After"]) > 0)
        self.assertIn("Too many questions", json.loads(raw)["error"])
        self.assertEqual(ask.call_count, 1)

    def test_invalid_requests_do_not_use_the_limit(self) -> None:
        limiter = AskLimiter(per_client=(1, 600))
        with mock.patch.object(server, "ASK_LIMITER", limiter):
            self.assertEqual(self.ask(piece_id="nope")[0], 400)
            with mock.patch.object(server.qa, "ask", return_value=dict(ANSWER)):
                self.assertEqual(self.ask()[0], 200)

    def test_keep_alive_connection_serves_consecutive_questions(self) -> None:
        import http.client

        host, port = self.server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=10)
        self.addCleanup(connection.close)
        body = json.dumps({"piece_id": "die-forelle", "question": "어떻게 부르나요?"})
        with mock.patch.object(server.qa, "ask", return_value=dict(ANSWER)):
            for _ in range(3):
                connection.request(
                    "POST", "/api/ask", body=body.encode(),
                    headers={"Content-Type": "application/json", "Origin": PAGES_ORIGIN},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["answer"], "ok")
                self.assertNotEqual(response.getheader("Connection"), "close")

    def test_oversized_body_is_rejected(self) -> None:
        body = b"{" + b" " * (server.MAX_ASK_BODY_BYTES + 1) + b"}"
        with mock.patch.object(server.qa, "ask") as ask:
            status, _, _ = self.request("POST", "/api/ask", body)
        self.assertEqual(status, 413)
        ask.assert_not_called()

    def test_remote_model_failures_are_service_unavailable(self) -> None:
        class RemoteFailure(RuntimeError):
            pass

        with (
            mock.patch.object(server.qa, "GENERATION_FAILED_ERRORS", (RemoteFailure,)),
            mock.patch.object(server.qa, "ask", side_effect=RemoteFailure("504")),
            mock.patch.object(server.traceback, "print_exc"),
        ):
            status, headers, raw = self.ask()
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(raw)["error"], server.REMOTE_MODEL_UNAVAILABLE)
        self.assertEqual(headers["Access-Control-Allow-Origin"], PAGES_ORIGIN)

    def test_health_reports_the_model(self) -> None:
        status, headers, raw = self.request(
            "GET", "/api/health", headers={"Origin": PAGES_ORIGIN}
        )
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertIn("backend", payload["model"])
        self.assertEqual(headers["Access-Control-Allow-Origin"], PAGES_ORIGIN)

    def test_health_waits_for_the_warm_up_when_enabled(self) -> None:
        server.WARMED_UP.clear()
        self.addCleanup(server.WARMED_UP.clear)
        timer = threading.Timer(0.3, server.WARMED_UP.set)
        with mock.patch.dict(os.environ, {"SOPRANO_LAB_WARMUP": "1"}):
            timer.start()
            status, _, raw = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["ready"])
        with (
            mock.patch.dict(os.environ, {"SOPRANO_LAB_WARMUP": "1"}),
            mock.patch.object(server, "HEALTH_WARMUP_WAIT_SECONDS", 0.05),
        ):
            server.WARMED_UP.clear()
            self.assertFalse(json.loads(self.request("GET", "/api/health")[2])["ready"])

    def test_forwarded_client_address_needs_opt_in(self) -> None:
        limiter = AskLimiter(per_client=(1, 600))
        seen = []
        original = limiter.acquire

        def record(client):
            seen.append(client)
            return original(client)

        limiter.acquire = record
        forwarded = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
        body = json.dumps({"piece_id": "die-forelle", "question": "어떻게 부르나요?"})
        with (
            mock.patch.object(server, "ASK_LIMITER", limiter),
            mock.patch.object(server.qa, "ask", return_value=dict(ANSWER)),
        ):
            self.request("POST", "/api/ask", body.encode(), forwarded)
            with mock.patch.dict(os.environ, {"SOPRANO_LAB_TRUST_PROXY": "1"}):
                self.request("POST", "/api/ask", body.encode(), forwarded)
        self.assertEqual(seen, ["127.0.0.1", "5.6.7.8"])


class RecordsHttpTests(ServerTestCase):
    """Question and feedback records (SOPRANO_LAB_RECORDS=1)."""

    def setUp(self) -> None:
        super().setUp()
        self.stdout = io.StringIO()
        for patcher in (
            mock.patch.dict(os.environ, {"SOPRANO_LAB_RECORDS": "1"}),
            mock.patch("sys.stdout", self.stdout),
            mock.patch.object(server, "FEEDBACK_LIMITER", AskLimiter(per_client=(3, 600))),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def records(self):
        return [json.loads(line) for line in self.stdout.getvalue().splitlines()]

    def feedback(self, **overrides):
        body = {"piece_id": "die-forelle", "language": "en", "rating": "up", **overrides}
        return self.request(
            "POST", "/api/feedback", json.dumps(body).encode("utf-8"),
            {"Origin": PAGES_ORIGIN, "X-Forwarded-For": "9.9.9.9"},
        )

    def test_health_says_records_are_kept(self) -> None:
        self.assertTrue(json.loads(self.request("GET", "/api/health")[2])["records"])
        with mock.patch.dict(os.environ, {"SOPRANO_LAB_RECORDS": ""}):
            self.assertFalse(json.loads(self.request("GET", "/api/health")[2])["records"])

    def test_saved_question_is_recorded_without_the_client(self) -> None:
        answer = {**ANSWER, "evidence": [{"id": "die-forelle-ku-002"}], "answer_basis": "retrieved_evidence"}
        with mock.patch.object(server.qa, "ask", return_value=answer):
            status, _, raw = self.ask(save=True, measure_range=[2, 27], language="en")
        self.assertEqual(status, 200)
        (record,) = self.records()
        self.assertEqual(json.loads(raw)["record_id"], record["record_id"])
        self.assertEqual(record["event"], server.QUESTION_EVENT)
        self.assertEqual(record["question"], "어떻게 부르나요?")
        self.assertEqual(record["measure_range"], [2, 27])
        self.assertEqual(record["answer"], "ok")
        self.assertEqual(record["evidence_ids"], ["die-forelle-ku-002"])
        self.assertNotIn("127.0.0.1", self.stdout.getvalue())

    def test_unsaved_question_is_not_recorded(self) -> None:
        with mock.patch.object(server.qa, "ask", return_value=dict(ANSWER)):
            for overrides in ({}, {"save": False}):
                status, _, raw = self.ask(**overrides)
                self.assertEqual(status, 200)
                self.assertNotIn("record_id", json.loads(raw))
            self.assertEqual(self.ask(save="yes")[0], 400)
        self.assertEqual(self.records(), [])

    def test_questions_are_not_recorded_when_records_are_off(self) -> None:
        with (
            mock.patch.dict(os.environ, {"SOPRANO_LAB_RECORDS": ""}),
            mock.patch.object(server.qa, "ask", return_value=dict(ANSWER)),
        ):
            self.assertNotIn("record_id", json.loads(self.ask(save=True)[2]))
            self.assertEqual(self.feedback()[0], 404)
        self.assertEqual(self.records(), [])

    def test_feedback_is_recorded(self) -> None:
        question_id = "a" * 32
        status, headers, raw = self.feedback(
            record_id=question_id, rating="down", comment="  Too vague.  "
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], PAGES_ORIGIN)
        (record,) = self.records()
        self.assertEqual(json.loads(raw)["record_id"], record["record_id"])
        self.assertEqual(record["event"], server.FEEDBACK_EVENT)
        self.assertEqual(record["question_record_id"], question_id)
        self.assertEqual(record["rating"], "down")
        self.assertEqual(record["comment"], "Too vague.")
        self.assertNotIn("9.9.9.9", self.stdout.getvalue())

    def test_feedback_on_a_precomputed_answer(self) -> None:
        status, _, _ = self.feedback(answer_file="answers/die-forelle/3.ko.json", rating=None, comment="좋아요")
        self.assertEqual(status, 200)
        self.assertEqual(self.records()[0]["answer_file"], "answers/die-forelle/3.ko.json")

    def test_invalid_feedback_is_rejected(self) -> None:
        for overrides in (
            {"rating": "meh"},
            {"rating": None, "comment": "   "},
            {"comment": "x" * (server.MAX_FEEDBACK_COMMENT_CHARS + 1)},
            {"record_id": "not-an-id"},
            {"answer_file": "../secrets.json"},
            {"piece_id": "nope"},
            {"language": "fr"},
        ):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.feedback(**overrides)[0], 400)
        self.assertEqual(self.records(), [])

    def test_feedback_is_limited(self) -> None:
        for _ in range(3):
            self.assertEqual(self.feedback()[0], 200)
        status, headers, _ = self.feedback()
        self.assertEqual(status, 429)
        self.assertTrue(int(headers["Retry-After"]) > 0)
        self.assertEqual(len(self.records()), 3)


if __name__ == "__main__":
    unittest.main()
